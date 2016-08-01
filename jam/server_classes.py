# -*- coding: utf-8 -*-

from __future__ import with_statement
import sys, os

import Queue
import multiprocessing
import threading
import zipfile
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape
import datetime, time
import traceback
import inspect
import json

import common, db.db_modules as db_modules
from items import *
from dataset import *
from sql import *
import adm_server

class ServerDataset(Dataset, SQL):
    def __init__(self, table_name='', soft_delete=True):
        Dataset.__init__(self)
        self.ID = None
        self.table_name = table_name
        self._order_by = []
        self.values = None
        self.on_open = None
        self.on_apply = None
        self.on_count = None
        self.on_get_field_text = None
        self.deleted_field_name = None
        self.soft_delete = soft_delete

    def copy(self, filters=True, details=True, handlers=True):
        result = super(ServerDataset, self).copy(filters, details, handlers)
        result.table_name = self.table_name
        result._order_by = self._order_by
        result.soft_delete = self.soft_delete
        result._primary_key = self._primary_key
        result._deleted_flag = self._deleted_flag
        result._master_id = self._master_id
        result._master_rec_id = self._master_rec_id
        return result

    def get_event(self, caption):
        return getattr(caption)

    def add_field(self, field_id, field_name, field_caption, data_type, required = False,
        item = None, object_field = None, visible = True, index=0, edit_visible = True, edit_index = 0, read_only = False,
        expand = False, word_wrap = False, size = 0, default_value=None, default = False, calculated = False, editable = False,
        master_field = None, alignment=None, lookup_values=None, enable_typeahead=False, field_help=None, field_placeholder=None):

        field_def = self.add_field_def(field_id, field_name, field_caption, data_type, required, item, object_field, visible,
            index, edit_visible, edit_index, read_only, expand, word_wrap, size, default_value, default, calculated, editable,
            master_field, alignment, lookup_values, enable_typeahead, field_help, field_placeholder)
        field = DBField(self, field_def)
        self._fields.append(field)
        return field

    def add_filter(self, name, caption, field_name, filter_type = common.FILTER_EQ,
        data_type = None, visible = True, filter_help=None, filter_placeholder=None):

        filter_def = self.add_filter_def(name, caption, field_name, filter_type,
            data_type, visible, filter_help, filter_placeholder)
        fltr = DBFilter(self, filter_def)
        self.filters.append(fltr)
        return fltr

    def do_internal_open(self, params):
        return self.select_records(params)

    def do_apply(self, params=None):
        if not self.master and self.log_changes:
            if self.item_state != common.STATE_BROWSE:
                raise Exception, u'Item: %s is not in browse state. Apply requires browse state.'
            changes = {}
            self.change_log.get_changes(changes)
            if changes['data']:
                data, error = self.apply_changes((changes, params),
                    {'can_view': True, 'can_create': True, 'can_edit': True, 'can_delete': True})
                if error:
                    raise Exception, error
                else:
                    self.change_log.update(data)

    def add_detail(self, table):
        detail = Detail(self, table.item_name, table.item_caption, table.table_name)
        self.details.append(detail)
        detail.owner = self
        detail.init_fields()
        return detail

    def detail_by_name(self, caption):
        for table in self.details:
            if table.item_name == caption:
                return table

    def change_order(self, *fields):
        self._order_by = []
        for field in fields:
            field_name = field
            desc = False
            if field[0] == '-':
                desc = True
                field_name = field[1:]
            try:
                fld = self._field_by_name(field_name)
            except:
                raise RuntimeError('%s: change_order method arument error - %s' % (self.item_name, field))
            self._order_by.append([fld.ID, desc])
        return self

    def get_record_count(self, params, user_info=None, enviroment=None):
        result = 0;
        if self.on_count:
            result, error_mes = self.on_count(self, params, user_info, enviroment)
        else:
            error_mes = ''
            result = 0
            sql = self.get_record_count_query(params)
            rows = self.task.execute_select(sql)
            result = rows[0][0]
        return result, error_mes

    def select_records(self, params, user_info=None, enviroment=None):
        if self.on_open:
            rows, error_mes = self.on_open(self, params, user_info, enviroment)
        else:
            sql = self.get_select_statement(params)
            error_mes = ''
            rows = []
            rows = self.task.execute_select(sql)
        return rows, error_mes

    def apply_delta(self, delta, privileges):
        sql = delta.apply_sql(privileges)
        return self.task.execute(sql)

    def apply_changes(self, data, privileges, user_info=None, enviroment=None):
        error = None
        result = None
        changes, params = data
        if not params:
            params = {}
        delta = self.delta(changes)
        if self.on_apply:
            result, error = self.on_apply(self, delta, params, privileges, user_info, enviroment)
        else:
            result, error = self.apply_delta(delta, privileges)
        return result, error

    def update_deleted(self):
        if self._is_delta and len(self.details):
            rec_no = self.rec_no
            try:
                for it in self:
                    if it.rec_deleted():
                        for detail in self.details:
                            fields = []
                            for field in detail.fields:
                                fields.append(field.field_name)
                            det = self.task.item_by_name(detail.item_name).copy()
                            where = {
                                det._master_id: self.ID,
                                det._master_rec_id: self._primary_key_field.value
                            }
                            det.open(fields=fields, expanded=detail.expanded, where=where)
                            it.edit()
                            for d in det:
                                detail.append()
                                for field in detail.fields:
                                    f = det.field_by_name(field.field_name)
                                    field.set_value(f.value, f.lookup_value)
                                detail.post()
                            it.post()
                            for d in detail:
                                d.record_status = common.RECORD_DELETED
            finally:
                self.rec_no = rec_no

    def field_by_id(self, id_value, field_name):
        return self.get_field_by_id((id_value, field_name))

    def get_field_by_id(self, params):
        id_value, fields = params
        if not (isinstance(fields, tuple) or isinstance(fields, list)):
            fields = [fields]
        copy = self.copy()
        copy.set_where(id=id_value)
        copy.open(fields=fields)
        if copy.record_count() == 1:
            result = []
            for field_name in fields:
                result.append(copy.field_by_name(field_name).value)
            if len(fields) == 1:
                return result[0]
            else:
                return result
        return

class Item(AbstrItem, ServerDataset):
    def __init__(self, owner, name, caption, visible = True,
            table_name='', view_template='', js_filename='', soft_delete=True):
        AbstrItem.__init__(self, owner, name, caption, visible, js_filename=js_filename)
        ServerDataset.__init__(self, table_name, soft_delete)
        self.item_type_id = None
        self.reports = []

    def get_reports_info(self):
        result = []
        for report in self.reports:
            result.append(report.ID)
        return result


class Param(DBField):
    def __init__(self, owner, param_def):
        DBField.__init__(self, owner, param_def)
        self.field_kind = common.PARAM_FIELD
        if self.data_type == common.TEXT:
            self.field_size = 1000
        else:
            self.field_size = 0
        self.param_name = self.field_name
        self.param_caption = self.field_caption
        self._value = None
        self._lookup_value = None
        setattr(owner, self.param_name, self)


    def system_field(self):
        return False

    def get_data(self):
        return self._value

    def set_data(self, value):
        self._value = value

    def get_lookup_data(self):
        return self._lookup_value

    def set_lookup_data(self, value):
        self._lookup_value = value

    def _do_before_changed(self):
        pass

    def _change_lookup_field(self, lookup_value=None, slave_field_values=None):
        pass

    def raw_display_text(self):
        result = ''
        if self.lookup_item:
            result = self.lookup_text
        else:
            result = self.text
        return result

    def copy(self, owner):
        result = Param(owner, self.param_caption, self.field_name, self.data_type,
            self.lookup_item, self.lookup_field, self.required,
            self.edit_visible, self.alignment)
        return result


class Report(AbstrReport):
    def __init__(self, owner, name='', caption='', visible = True,
            table_name='', view_template='', js_filename=''):
        AbstrReport.__init__(self, owner, name, caption, visible, js_filename=js_filename)
        self.param_defs = []
        self.params = []
        self.template = view_template
        self.template_name = None
        self.template_content = {}

        self.on_before_generate = None
        self.on_generate = None
        self.on_after_generate = None
        self.on_parsed = None
        self.on_before_save_report = None

        self.on_before_append = None
        self.on_after_append = None
        self.on_before_edit = None
        self.on_after_edit = None
        self.on_before_open = None
        self.on_after_open = None
        self.on_before_post = None
        self.on_after_post = None
        self.on_before_delete = None
        self.on_after_delete = None
        self.on_before_cancel = None
        self.on_after_cancel = None
        self.on_before_apply = None
        self.on_after_apply = None
        self.on_before_scroll = None
        self.on_after_scroll = None
        self.on_filter_record = None
        self.on_field_changed = None
        self.on_filters_applied = None
        self.on_before_field_changed = None
        self.on_filter_value_changed = None
        self.on_field_validate = None
        self.on_get_field_text = None


    def add_param(self, caption='', name='', data_type=common.INTEGER,
        obj=None, obj_field=None, required=True, visible=True, alignment=None,
        enable_typeahead=None, lookup_values=None, param_help=None, param_placeholder=None):
        param_def = self.add_param_def(caption, name, data_type, obj,
            obj_field, required, visible, alignment, enable_typeahead,
            lookup_values, param_help, param_placeholder)
        param = Param(self, param_def)
        self.params.append(param)

    def add_param_def(self, param_caption='', param_name='', data_type=common.INTEGER,
        lookup_item=None, lookup_field=None, required=True, visible=True,
        alignment=0, enable_typeahead=False, lookup_values=None, param_help=None,
        param_placeholder=None):
        param_def = [None for i in range(len(FIELD_DEF))]
        param_def[FIELD_NAME] = param_name
        param_def[NAME] = param_caption
        param_def[FIELD_DATA_TYPE] = data_type
        param_def[REQUIRED] = required
        param_def[LOOKUP_ITEM] = lookup_item
        param_def[LOOKUP_FIELD] = lookup_field
        param_def[FIELD_EDIT_VISIBLE] = visible
        param_def[FIELD_ALIGNMENT] = alignment
        param_def[FIELD_ENABLE_TYPEAHEAD] = enable_typeahead
        param_def[FIELD_LOOKUP_VALUES] = lookup_values
        param_def[FIELD_HELP] = param_help
        param_def[FIELD_PLACEHOLDER] = param_placeholder
        self.param_defs.append(param_def)
        return param_def

    def prepare_params(self):
        for param in self.params:
            if param.lookup_item and type(param.lookup_item) == int:
                param.lookup_item = self.task.item_by_ID(param.lookup_item)
            if param.lookup_field and type(param.lookup_field) == int:
                param.lookup_field = param.lookup_item._field_by_ID(param.lookup_field).field_name

    def copy(self):
        result = self.__class__(self.owner, self.item_name, self.item_caption, self.visible,
            '', self.template, '');
        result.on_before_generate = self.on_before_generate
        result.on_generate = self.on_generate
        result.on_after_generate = self.on_after_generate
        result.on_before_save_report = self.on_before_save_report
        result.on_parsed = self.on_parsed
        result.param_defs = self.param_defs
        result.template_content = self.template_content.copy()
        result.template_name = self.template_name
        for param_def in result.param_defs:
            param = Param(result, param_def)
            result.params.append(param)
        result.prepare_params()
        return  result

    def print_report(self, param_values, url, ext=None):
        if not self.template_content:
            self.parse_template()
        copy_report = self.copy()
        return copy_report.generate(param_values, url, ext)

    def generate_file_name(self, ext=None):
        if not ext:
            ext = 'ods'
        file_name = self.item_name + '_' + datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S.%f') + '.' + ext
        #~ if os.name == "nt":
            #~ file_name = self.item_name + '_' + datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S.%f') + '.' + ext
        #~ else:
            #~ file_name = self.item_caption + '_' + datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S.%f') + '.' + ext
        file_name = escape(file_name, {':': '-', '/': '_', '\\': '_'})
        return os.path.abspath(os.path.join(self.task.work_dir, 'static', 'reports', file_name))

    def generate(self, param_values, url, ext):
        self.extension = ext
        self.url = url
        template = self.template
        for i, param in enumerate(self.params):
            param.value = param_values[i]
        if self.on_before_generate:
            self.on_before_generate(self)
        if template != self.template:
            self.template_content = None
        if self.template:
            if not self.template_content:
                self.parse_template()
            if self.on_parsed:
                self.on_parsed(self)
            self.content_name = os.path.join(self.task.work_dir, 'reports', 'content%s.xml' % time.time())
            self.content = open(self.content_name, 'wb')
            try:
                self.report_filename = self.generate_file_name()
                file_name = os.path.basename(self.report_filename)
                static_dir = os.path.dirname(self.report_filename)
                if not os.path.exists(static_dir):
                    os.makedirs(static_dir)
                self.content.write(self.template_content['header'])
                self.content.write(self.template_content['columns'])
                self.content.write(self.template_content['rows'])
                if self.on_generate:
                    self.on_generate(self)
                self.content.write(self.template_content['footer'])
                self.save()
            finally:
                if not self.content.closed:
                    self.content.close()
                if os.path.exists(self.content_name):
                    os.remove(self.content_name)
            if ext and (ext != 'ods'):
                converted = False
                if self.owner.on_convert_report:
                    try:
                        self.owner.on_convert_report(self)
                        converted = True
                    except:
                        pass
                #~ if not converted:
                    #~ # OpenOffice must be running in server mode
                    #~ # soffice --headless --accept="socket,host=127.0.0.1,port=2002;urp;"
                    #~ ext_file_name = self.report_filename.replace('.ods', '.' + ext)
                    #~ try:
                        #~ from DocumentConverter import DocumentConverter
                        #~ converter = DocumentConverter()
                        #~ converter.convert(self.report_filename, ext_file_name)
                        #~ converted = True
                    #~ except:
                        #~ pass
                if not converted:
                    converted = self.task.convert_report(self, ext)
                converted_file = self.report_filename.replace('.ods', '.' + ext)
                if converted and os.path.exists(converted_file):
                    self.delete_report(self.report_filename)
                    file_name = file_name.replace('.ods', '.' + ext)
            self.report_filename = os.path.join(self.task.work_dir, 'static', 'reports', file_name)
            self.report_url = self.report_filename
            if self.url:
                self.report_url = os.path.join(self.url, 'static', 'reports', file_name)
        else:
            if self.on_generate:
                self.on_generate(self)
        if self.on_after_generate:
            self.on_after_generate(self)
        return self.report_url

    def delete_report(self, file_name):
        report_name = os.path.join(self.task.work_dir, 'static', 'reports', file_name)
        os.remove(report_name)

    def parse_template(self):
        if not os.path.isabs(self.template):
            self.template_name = os.path.join(self.task.work_dir, 'reports', self.template)
        else:
            self.template_name = self.template
        z = zipfile.ZipFile(self.template_name, 'r')
        try:
            data = z.read('content.xml')
        finally:
            z.close()
        band_tags = []
        bands = {}
        colum_defs = []
        header = ''
        columns = ''
        rows = ''
        footer = ''
        repeated_rows = None
        if data:
            dom = parseString(data)
            try:
                tables = dom.getElementsByTagName('table:table')
                if len(tables) > 0:
                    table = tables[0]
                    for child in table.childNodes:
                        if child.nodeName == 'table:table-column':
                            repeated = child.getAttribute('table:number-columns-repeated')
                            if not repeated:
                                repeated = 1
                            colum_defs.append(['', repeated])
                        if child.nodeName == 'table:table-row':
                            repeated = child.getAttribute('table:number-rows-repeated')
                            if repeated and repeated.isdigit():
                                repeated_rows = str(repeated)
                            for row_child in child.childNodes:
                                if row_child.nodeName == 'table:table-cell':
                                    text = row_child.getElementsByTagName('text:p')
                                    if text.length > 0:
                                        band_tags.append(text[0].childNodes[0].nodeValue)
                                    break
                start = 0
                columns_start = 0
                for col in colum_defs:
                    start = data.find('<table:table-column', start)
                    if columns_start == 0:
                        columns_start = start
                    end = data.find('/>', start)
                    col_text = data[start: end + 2]
                    columns += col_text
                    col[0] = data[start: end + 2]
                    start = end + 2
                columns_end = start
                header = data[0:columns_start]
                assert len(band_tags) > 0, u'No bands in report template'
                positions = []
                start = 0
                for tag in band_tags:
                    text = str('>%s<' % tag)
                    i = data.find(text)
                    i = data.rfind('<table:table-row', start, i)
                    positions.append(i)
                    start = i
                if repeated_rows and int(repeated_rows) > 1000:
                    i = data.find(repeated_rows)
                    i = data.rfind('<table:table-row', start, i)
                    band_tags.append('$$$end_of_report')
                    positions.append(i)
                rows = data[columns_end:positions[0]]
                for i, tag in enumerate(band_tags):
                    start = positions[i]
                    try:
                        end = positions[i + 1]
                    except:
                        end = data.find('</table:table>', start)
                    bands[tag] = data[start: end].replace(str(tag), '')
                footer = data[end:len(data)]
                self.template_content = {}
                self.template_content['bands'] = bands
                self.template_content['colum_defs'] = colum_defs
                self.template_content['header'] = header
                self.template_content['columns'] = columns
                self.template_content['rows'] = rows
                self.template_content['footer'] = footer
            finally:
                dom.unlink()
                del(dom)

    def hide_columns(self, col_list):

        def convert_str_to_int(s):
            base = ord('A')
            mult = ord('Z') - base + 1
            result = s
            if type(s) == str:
                result = 0
                chars = []
                for i in range(len(s)):
                    chars.append(s[i])
                for i in range(len(chars) - 1, -1, -1):
                    result += (ord(chars[i]) - base + 1) * mult ** i
            return result

        def remove_repeated(col, repeated):
            result = col
            p = col.find('table:number-columns-repeated')
            if p != -1:
                r = col.find(str(repeated), p)
                if r != -1:
                    for i in xrange(r, 100):
                        if col[i] in ("'", '"'):
                            result = col.replace(col[p:i+1], '')
                            break
            return result

        if self.template_content:
            ints = []
            for i in col_list:
                ints.append(convert_str_to_int(i))
            colum_defs = self.template_content['colum_defs']
            columns = ''
            index = 1
            for col, repeated in colum_defs:
                repeated = int(repeated)
                if repeated > 1:
                    col = remove_repeated(col, repeated)
                for i in range(repeated):
                    cur_col = col
                    if index in ints:
                        cur_col = cur_col[0:-2] + ' table:visibility="collapse"/>'
                    columns += cur_col
                    index += 1
            self.template_content['colum_defs'] = colum_defs
            self.template_content['columns'] = columns

    def print_band(self, band, dic=None, update_band_text=None):
        text = self.template_content['bands'][band]
        if dic:
            d = dic.copy()
            for key, value in d.items():
                if type(value) in (str, unicode):
                    d[key] = escape(value)
            cell_start = 0
            cell_start_tag = '<table:table-cell'
            cell_type_tag = 'office:value-type="string"'
            calcext_type_tag = 'calcext:value-type="string"'
            start_tag = '<text:p>'
            end_tag = '</text:p>'
            while True:
                cell_start = text.find(cell_start_tag, cell_start)
                if cell_start == -1:
                    break
                else:
                    start = text.find(start_tag, cell_start)
                    if start != -1:
                        end = text.find(end_tag, start + len(start_tag))
                        if end != -1:
                            text_start = start+len(start_tag)
                            text_end = end
                            cell_text = text[text_start:text_end]
                            cell_text_start = cell_text.find('%(', 0)
                            if cell_text_start != -1:
                                end = cell_text.find(')s', cell_text_start + 2)
                                if end != -1:
                                    end += 2
                                    val = cell_text[cell_text_start:end]
                                    key = val[2:-2]
                                    value = d.get(key)
                                    if not value is None:
                                        val = val % d
                                        if type(value) == float:
                                            val = val.replace('.', common.DECIMAL_POINT)
                                    else:
                                        if not key in d.keys():
                                            print 'Report: "%s" band: "%s" key "%s" not found in the dictionary' % (self.item_name, band, key)
                                    if  isinstance(cell_text, unicode):
                                        cell_text = cell_text.encode('utf8')
                                    if  isinstance(val, unicode):
                                        val = val.encode('utf8')
                                    cell_text = cell_text[:cell_text_start] + val + cell_text[end:]
                                    text = text[:text_start] + cell_text + text[text_end:]
                                    if type(value) in (int, float):
                                        start_text = text[cell_start:start]
                                        office_value = str(value)
                                        start_text = start_text.replace(cell_type_tag, 'office:value-type="float" office:value="%s"' % office_value)
                                        start_text = start_text.replace(calcext_type_tag, 'calcext:value-type="float"')
                                        text = text[:cell_start] + start_text + text[start:]
                    cell_start += 1
            if update_band_text:
                text = update_band_text(text)
        self.content.write(text)

    def save(self):
        self.content.close()
        z = None
        self.zip_file = None
        try:
            self.zip_file = zipfile.ZipFile(self.report_filename, 'w', zipfile.ZIP_DEFLATED)
            z = zipfile.ZipFile(self.template_name, 'r')
            if self.on_before_save_report:
                self.on_before_save_report(self)
            for file_name in z.namelist():
                data = z.read(file_name)
                if file_name == 'content.xml':
                    self.zip_file.write(self.content_name, file_name)
                else:
                    self.zip_file.writestr(file_name, data)
        finally:
            if z:
                z.close()
            if self.zip_file:
                self.zip_file.close()

    def cur_to_str(self, value):
        return common.cur_to_str(value)

    def date_to_str(self, value):
        return common.date_to_str(value)

    def datetime_to_str(self, value):
        return common.datetime_to_str(value)

    def _set_modified(self, value):
        pass

log = {}
log_counter = 0
log_save_after = 0
log_pid = os.getpid()

def find_where_fields(command):
    result = ''
    where_pos = command.find('WHERE')
    where = None
    if where_pos >= 0:
        a = []
        where = command[where_pos + 5:]
        a.append(where.find(' GROUP BY'))
        a.append(where.find(' ORDER BY'))
        a.append(where.find(' HAVING'))
        pos = -1
        b = []
        for p in a:
            if p >= 0:
                b.append(p)
        if len(b):
            pos = min(b)
        if pos >= 0:
            where = where[:pos + 1]
        if where:
            parts = where.split('AND')
            fields = []
            for p in parts:
                p = p.strip()
                p = p.strip('(')
                w = ''
                for ch in p:
                    if ch in [' ', '>', '<', '=']:
                        break
                    else:
                        w += ch
                w = w.replace('"', '')
                pos = w.find('.')
                if pos >= 0:
                    w = w[pos + 1:]
                fields.append(w)
#            fields.sort()
            result = ' '.join(fields)
    return result

def find_table_name(command):
    result = ''
    s = ''
    if command[:12] ==   'SELECT NEXT ':
        result = ''
    elif command[:12] == 'INSERT INTO ':
        result = ''
    elif command[:6] == 'UPDATE':
        s = command[7:]
        s = s.lstrip('"')
    else:
        pos = command.find(' FROM ')
        s = command[pos+5:pos+105]
        s = s.lstrip(' ')
        s = s.lstrip('(')
        s = s.lstrip('"')
    if s:
        w = ''
        for ch in s:
            if ch in [' ', '"']:
                break
            else:
                w += ch
        result = w
    return result

def save_log(log):

    def get_reqs_key(item):
        key, (dur, freq) = item
        return dur / 1000000.0 / freq

    def get_tables_key(item):
        key, (dur, freq, reqs) = item
        return dur / 1000000.0 / freq

    text = ''

    tlist = sorted(log.iteritems(), key=get_tables_key, reverse=True)
    for table, (dur, freq, reqs) in tlist:
        sec = dur / 1000000.0
        per_request = sec / freq
        text += '%-20s %f %d %f\n' % (table, per_request, freq, sec)

    text += '\n'

    tables = sorted(log.keys())
    for table in tables:
        dur, freq, reqs = log[table]
        sec = dur / 1000000.0
        per_request = sec / freq
        text += '\n%-20s %f %d %f\n' % (table, per_request, freq, sec)
        rlist = sorted(reqs.iteritems(), key=get_reqs_key, reverse=True)
        for fields, (dur, freq) in rlist:
            sec = dur / 1000000.0
            per_request = sec / freq
            text += '      %f %d %f %s\n' % (per_request, freq, sec, fields)
    with open('log.info', 'w') as f:
        f.write(text.encode('utf-8'))


def log_request(command, duration):
    global log_counter
    global log_pid
    global log
    global log_save_after

    if log_pid == os.getpid():
        command = command.upper().strip()
        table_name = find_table_name(command)
        if table_name:
            where_fields = find_where_fields(command)

            msec = duration.microseconds

            table = log.get(table_name)
            if not table:
                table = [0, 0, {}]
                log[table_name] = table
            table[0] += msec
            table[1] += 1
            reqs = table[2]

            req = reqs.get(where_fields)
            if not req:
                req = [0, 0]
                reqs[where_fields] = req
            req[0] += msec
            req[1] += 1

        log_counter += 1
        if log_counter % log_save_after == 0:
            save_log(log)


def execute_sql(db_module, db_database, db_user, db_password,
    db_host, db_port, db_encoding, connection, command,
    params=None, result_set=None, call_proc=False, commit=True):

    def execute_command(cursor, command, params=None):
        global log_save_after
        print_command = False
        log_command = False
        try:
            if print_command:
                print ''
                print command
                print params

            now = datetime.datetime.now()

            result = None
            if params:
                cursor.execute(command, params)
            else:
                cursor.execute(command)
            if result_set == 'ONE':
                result = cursor.fetchone()
            elif result_set == 'ALL':
                result = cursor.fetchall()

            if log_save_after:
                log_request(command, datetime.datetime.now() - now)

            if log_command and command.upper().find('SELECT') == -1:
                try:
                    with open("sql_log.txt", "a") as f:
                        f.write('\n')
                        f.write(command + '\n')
                        f.write(json.dumps(params, default=common.json_defaul_handler) + '\n')
                except:
                    pass

            return result
        except Exception, x:
            print '\nError: %s\n command: %s\n params: %s' % (str(x), command, params)
            raise

    def get_next_id(cursor, sql):
        cursor.execute(sql)
        rec = cursor.fetchone()
        return int(rec[0])

    def execute_delta(cursor, command, delta_result):

        def process_delta(delta, master_rec_id, result):
            ID, sqls = delta
            result['ID'] = ID
            changes = []
            result['changes'] = changes
            for sql in sqls:
                (command, params, info), details = sql
                if info:
                    rec_id = info['primary_key']
                    if info['status'] == common.RECORD_INSERTED:
                        if rec_id:
                            pass
                        else:
                            next_sequence_value_sql = db_module.next_sequence_value_sql(info['table_name'])
                            if next_sequence_value_sql:
                                rec_id = get_next_id(cursor, next_sequence_value_sql)
                                params[info['primary_key_index']] = rec_id
                    if info['status'] == common.RECORD_INSERTED and info['master_rec_id_index']:
                        params[info['master_rec_id_index']] = master_rec_id
                    if command:
                        execute_command(cursor, command, params)
                    if not rec_id and info['status'] == common.RECORD_INSERTED:
                        new_id = db_module.get_lastrowid(cursor)
                        if new_id:
                            rec_id = new_id
                    result_details = []
                    if rec_id:
                        changes.append({'log_id': info['log_id'], 'rec_id': rec_id, 'details': result_details})
                    for detail in details:
                        result_detail = {}
                        result_details.append(result_detail)
                        process_delta(detail, rec_id, result_detail)
                else:
                    if command:
                        execute_command(cursor, command, params)

        delta = command['delta']
        process_delta(delta, None, delta_result)

    def execute_list(cursor, command, delta_result):
        res = None
        if command:
            for com in command:
                if com:
                    if isinstance(com, unicode) or isinstance(com, str):
                        res = execute_command(cursor, com)
                    elif isinstance(com, list):
                        res = execute_list(cursor, com, delta_result)
                    elif isinstance(com, dict):
                        res = execute_delta(cursor, com, delta_result)
                    elif isinstance(com, tuple):
                        res = execute_command(cursor, com[0], com[1])
                    else:
                        raise Exception, 'server_classes execute_list: invalid argument - command: %s' % command
            return res

    def execute(connection):
        delta_result = {}
        result = None
        error = None
        try:
            cursor = connection.cursor()
            if call_proc:
                try:
                    cursor.callproc(command, params)
                    result = cursor.fetchone()
                except Exception, x:
                    print '\nError: %s in command: %s' % (str(x), command)
                    raise
            else:
                if isinstance(command, str) or isinstance(command, unicode):
                    result = execute_command(cursor, command, params)
                elif isinstance(command, dict):
                    res = execute_delta(cursor, command, delta_result)
                elif isinstance(command, list):
                    result = execute_list(cursor, command, delta_result)
                elif isinstance(command, tuple):
                    result = execute_command(cursor, command[0], command[1])
            if commit:
                connection.commit()
            if delta_result:
                result = delta_result
        except Exception, x:
            try:
                if connection:
                    connection.rollback()
                    connection.close()
                error = str(x)
                if not error:
                    error = 'Execute error'
                print traceback.format_exc()
            finally:
                connection = None
        return connection, (result, error)

    if connection is None:
        connection = db_module.connect(db_database, db_user, db_password, db_host, db_port, db_encoding)
    return execute(connection)

def process_request(parentPID, name, queue, db_type, db_database, db_user, db_password, db_host, db_port, db_encoding, mod_count):
    con = None
    counter = 0
    db_module = db_modules.get_db_module(db_type)
    while True:
        if parentPID and hasattr(os, 'getppid') and os.getppid() != parentPID:
            break
        request = queue.get()
        if request:
            result_queue = request['queue']
            command = request['command']
            params = request['params']
            result_set = request['result_set']
            call_proc = request['call_proc']
            commit = request['commit']
            cur_mod_count = request['mod_count']
            if cur_mod_count != mod_count or counter > 1000:
                if con:
                    con.rollback()
                    con.close()
                con = None
                mod_count = cur_mod_count
                counter = 0
            con, result = execute_sql(db_module, db_database, db_user, db_password,
                db_host, db_port, db_encoding, con, command, params, result_set, call_proc, commit)
            counter += 1
            result_queue.put(result)

class Consts(object):
    def __init__(self):
        self.TEXT = common.TEXT
        self.INTEGER = common.INTEGER
        self.FLOAT = common.FLOAT
        self.CURRENCY = common.CURRENCY
        self.DATE = common.DATE
        self.DATETIME = common.DATETIME
        self.BOOLEAN = common.BOOLEAN
        self.BLOB = common.BLOB

        self.ITEM_FIELD = common.ITEM_FIELD
        self.FILTER_FIELD = common.FILTER_FIELD
        self.PARAM_FIELD = common.PARAM_FIELD

        self.FILTER_EQ = common.FILTER_EQ
        self.FILTER_NE = common.FILTER_NE
        self.FILTER_LT = common.FILTER_LT
        self.FILTER_LE = common.FILTER_LE
        self.FILTER_GT = common.FILTER_GT
        self.FILTER_GE = common.FILTER_GE
        self.FILTER_IN = common.FILTER_IN
        self.FILTER_NOT_IN = common.FILTER_NOT_IN
        self.FILTER_RANGE = common.FILTER_RANGE
        self.FILTER_ISNULL = common.FILTER_ISNULL
        self.FILTER_EXACT = common.FILTER_EXACT
        self.FILTER_CONTAINS = common.FILTER_CONTAINS
        self.FILTER_STARTWITH = common.FILTER_STARTWITH
        self.FILTER_ENDWITH = common.FILTER_ENDWITH
        self.FILTER_CONTAINS_ALL = common.FILTER_CONTAINS_ALL

        self.ALIGN_LEFT = common.ALIGN_LEFT
        self.ALIGN_CENTER = common.ALIGN_CENTER
        self.ALIGN_RIGHT = common.ALIGN_RIGHT

        self.STATE_INACTIVE = common.STATE_INACTIVE
        self.STATE_BROWSE = common.STATE_BROWSE
        self.STATE_INSERT = common.STATE_INSERT
        self.STATE_EDIT = common.STATE_EDIT
        self.STATE_DELETE = common.STATE_DELETE

        self.RECORD_UNCHANGED = common.RECORD_UNCHANGED
        self.RECORD_INSERTED = common.RECORD_INSERTED
        self.RECORD_MODIFIED = common.RECORD_MODIFIED
        self.RECORD_DETAILS_MODIFIED = common.RECORD_DETAILS_MODIFIED
        self.RECORD_DELETED = common.RECORD_DELETED


class AbstractServerTask(AbstrTask):
    def __init__(self, app, name, caption, js_filename, db_type,
        db_database = '', db_user = '', db_password = '', host='', port='',
        encoding='', con_pool_size=1, mp_pool=False, persist_con=False):
        AbstrTask.__init__(self, None, None, None, None)
        self.app = app
        self.consts = Consts()
        self.items = []
        self.lookup_lists = {}
        self.ID = None
        self.item_name = name
        self.item_caption = caption
        self.js_filename = js_filename
        self.db_type = db_type
        self.db_database = db_database
        self.db_user = db_user
        self.db_password = db_password
        self.db_host = host
        self.db_port = port
        self.db_encoding = encoding
        self.db_module = db_modules.get_db_module(self.db_type)
        self.on_request = None
        self.work_dir = os.getcwd()
        self.con_pool_size = 0
        self.mod_count = 0
        self.modules = []
        self.conversion_lock = threading.Lock()
        self.con_pool_size = con_pool_size
        self.mp_pool = mp_pool
        self.persist_con = persist_con
        self.persist_con_busy = 0
        if self.mp_pool:
            if self.persist_con:
                self.create_connection_pool(1)
            self.create_mp_connection_pool(self.con_pool_size)
        else:
            self.create_connection_pool(self.con_pool_size)

    def get_version(self):
        return common.SETTINGS['VERSION']

    version = property (get_version)

    def create_connection_pool(self, con_count):
        self.queue = Queue.Queue()
        pid = None
        for i in range(con_count):
            p = threading.Thread(target=process_request, args=(pid, self.item_name,
                self.queue, self.db_type, self.db_database, self.db_user,
                self.db_password, self.db_host, self.db_port,
                self.db_encoding, self.mod_count))
            p.daemon = True
            p.start()

    def create_mp_connection_pool(self, con_count):
        self.mp_queue = multiprocessing.Queue()
        self.mp_manager = multiprocessing.Manager()
        pid = os.getpid()
        for i in range(con_count):
            p = multiprocessing.Process(target=process_request, args=(pid, self.item_name,
                self.mp_queue, self.db_type, self.db_database, self.db_user,
                self.db_password, self.db_host, self.db_port,
                self.db_encoding, self.mod_count))
            p.daemon = True
            p.start()

    def create_connection(self):
        return self.db_module.connect(self.db_database, self.db_user, self.db_password, self.db_host, self.db_port, self.db_encoding)

    def send_to_pool(self, queue, result_queue, command, params=None, result_set=None, call_proc=False, commit=True):
        request = {}
        request['queue'] = result_queue
        request['command'] = command
        request['params'] = params
        request['result_set'] = result_set
        request['call_proc'] = call_proc
        request['commit'] = commit
        request['mod_count'] = self.mod_count
        queue.put(request)
        return  result_queue.get()

    def execute_in_pool(self, command, params=None, result_set=None, call_proc=False, commit=True):
        result_queue = Queue.Queue()
        result = self.send_to_pool(self.queue, result_queue, command, params, result_set, call_proc, commit)
        return result

    def execute_in_mp_poll(self, command, params=None, result_set=None, call_proc=False, commit=True):
        result_queue = self.mp_manager.Queue()
        result = self.send_to_pool(self.mp_queue, result_queue, command, params, result_set, call_proc, commit)
        return result

    def execute(self, command, params=None, result_set=None, call_proc=False, commit=True):
        if self.mp_pool:
            if self.persist_con and not self.persist_con_busy:
                self.persist_con_busy += 1
                try:
                    result = self.execute_in_pool(command, params, result_set, call_proc, commit)
                finally:
                    self.persist_con_busy -= 1
            else:
                result = self.execute_in_mp_poll(command, params, result_set, call_proc, commit)
        else:
            result = self.execute_in_pool(command, params, result_set, call_proc, commit)
        return result

    def callproc(self, command, params=None):
        result_set, error = self.execute(command, params, call_proc=True)
        if not error:
            return result_set

    def execute_select(self, command, params=None):
        result, error = self.execute(command, params, result_set='ALL', commit=False)
        if error:
            raise Exception, error
        else:
            return result

    def execute_select_one(self, command, params=None):
        result, error = self.execute(command, params, result_set='ONE', commit=False)
        if error:
            raise Exception, error
        else:
            return result

    def get_module_name(self):
        return str(self.item_name)

    def compile_item(self, item):
        item.module_name = None
        code = item.server_code
        item.module_name = item.get_module_name()
        item_module = type(sys)(item.module_name)
        item_module.__dict__['this'] = item
        sys.modules[item.module_name] = item_module

        item.task.modules.append(item.module_name)
        if item.owner:
            sys.modules[item.owner.get_module_name()].__dict__[item.module_name] = item_module
        if code:
            try:
                code = code.encode('utf-8')
            except Exception, e:
                print e
            comp_code = compile(code, item.module_name, "exec")
            exec comp_code in item_module.__dict__

            item_module.__dict__['__loader__'] = item._loader
            funcs = inspect.getmembers(item_module, inspect.isfunction)
            item._events = []
            for func_name, func in funcs:
                item._events.append((func_name, func))
                setattr(item, func_name, func)
        del code

    def login(self, params):
        return 1

    def add_item(self, item):
        self.items.append(item)
        item.owner = self
        return item

    def find_item(self, g_index, i_index):
        return self.items[g_index].items[i_index]

    def convert_report(self, report, ext):
        converted = False
        with self.conversion_lock:
            try:
                from subprocess import Popen, STDOUT, PIPE
                if os.name == "nt":
                    import _winreg
                    regpath = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\soffice.exe"
                    root = _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE, regpath)
                    s_office = _winreg.QueryValue(root, "")
                else:
                    s_office = "soffice"
                convertion = Popen([s_office, '--headless', '--convert-to', ext,
                    report.report_filename, '--outdir', os.path.join(self.work_dir, 'static', 'reports') ],
                    stderr=STDOUT,stdout = PIPE)#, shell=True)
                out, err = convertion.communicate()
                converted = True
            except Exception, e:
                print e
        return converted

    def copy_database(self, dbtype, database=None, user=None, password=None,
        host=None, port=None, encoding=None):
        connection = None
        limit = 1024
        db_module = db_modules.get_db_module(dbtype)
        for group in self.items:
            for it in group.items:
                if it.item_type != 'report':
                    item = it.copy(handlers=False, filters=False, details=False)
                    self.execute(self.db_module.set_case('DELETE FROM %s' % item.table_name))
                    item.open(expanded=False, open_empty=True)
                    params = {'__fields': [], '__filters': [], '__expanded': False, '__offset': 0, '__limit': 0}
                    sql = item.get_record_count_query(params, db_module)
                    connection, (result, error) = \
                    execute_sql(db_module, database, user, password,
                        host, port, encoding, connection, sql, None, 'ALL')
                    record_count = result[0][0]
                    loaded = 0
                    max_id = 0
                    if record_count:
                        while True:
                            params['__offset'] = loaded
                            params['__limit'] = limit
                            sql = item.get_select_statement(params, db_module)
                            connection, (result, error) = \
                            execute_sql(db_module, database, user, password,
                                host, port, encoding, connection, sql, None, 'ALL')
                            if not error:
                                for i, r in enumerate(result):
                                    item.append()
                                    j = 0
                                    for field in item.fields:
                                        if not field.master_field:
                                            field.value = r[j]
                                            j += 1
                                    if item._primary_key_field.value > max_id:
                                        max_id = item._primary_key_field.value
                                    item.post()
                                item.apply()
                            else:
                                raise Exception, error
                            records = len(result)
                            loaded += records
                            print 'coping table %s: %d%%' % (item.item_name, int(loaded * 100 / record_count))
                            if records == 0 or records < limit:
                                break
                        if self.db_module.restart_sequence_sql:
                            sql = self.db_module.restart_sequence_sql(item.table_name, max_id + 1)
                            self.execute(sql)

class Task(AbstractServerTask):
    def __init__(self, app, name, caption, js_filename,
        db_type, db_database = '', db_user = '', db_password = '',
        host='', port='', encoding='', con_pool_size=4, mp_pool=True,
        persist_con=True):
        AbstractServerTask.__init__(self, app, name, caption, js_filename,
            db_type, db_database, db_user, db_password,
            host, port, encoding, con_pool_size, mp_pool, persist_con)
        self.on_created = None
        self.on_login = None
        self.on_get_user_info = None
        self.on_logout = None
        self.on_ext_request = None
        self.init_dict = {}
        for key, value in self.__dict__.items():
            self.init_dict[key] = value

    def find_user(self, login, password_hash=None):
        return self.admin.find_user(self.app.admin, login, password_hash)


class AdminTask(AbstractServerTask):
    def __init__(self, app, name, caption, js_filename,
        db_type, db_database = '', db_user = '', db_password = '',
        host='', port='', encoding=''):
        AbstractServerTask.__init__(self, app, name, caption, js_filename,
            db_type, db_database, db_user, db_password, host, port, encoding, 2)
        filepath, filename = os.path.split(__file__)
        self.cur_path = filepath

    def create_task(self):
        return adm_server.create_task(self.app)

    def get_roles(self):
        return adm_server.get_roles(self)

    def reload_task(self):
        adm_server.reload_task(self)

    def update_events_code(self):
        adm_server.update_events_code(self)

    def logout(self, user_id):
        adm_server.logout(self, user_id)

class Group(AbstrGroup):
    def __init__(self, owner, name, caption, view_template=None, js_filename=None, visible=True, item_type_id=0):
        AbstrGroup.__init__(self, owner, name, caption, visible, item_type_id, js_filename)
        self.ID = None
        self.view_template = view_template
        self.js_filename = js_filename
        if item_type_id == common.REPORTS_TYPE:
            self.on_convert_report = None

    def add_catalog(self, name, caption, table_name, visible=True, view_template='', js_filename='', soft_delete=True):
        result = Item(self, name, caption, visible, table_name, view_template, js_filename, soft_delete)
        result.item_type_id = common.ITEM_TYPE
        return result

    def add_table(self, name, caption, table_name, visible=True, view_template='', js_filename='', soft_delete=True):
        result = Item(self, name, caption, visible, table_name, view_template, js_filename, soft_delete)
        result.item_type_id = common.TABLE_TYPE
        return result

    def add_report(self, name, caption, table_name, visible=True, view_template='', js_filename='', soft_delete=True):
        result = Report(self, name, caption, visible, table_name, view_template, js_filename)
        result.item_type_id = common.REPORT_TYPE
        return result


class Detail(AbstrDetail, ServerDataset):
    def __init__(self, owner, name, caption, table_name):
        AbstrDetail.__init__(self, owner, name, caption, True)
        ServerDataset.__init__(self, table_name)
        self.prototype = self.task.item_by_name(self.item_name)
        self.master = owner

    def init_fields(self):
        self.field_defs = []
        for field_def in self.prototype.field_defs:
            self.field_defs.append(list(field_def))
        for field_def in self.field_defs:
            field = DBField(self, field_def)
            self._fields.append(field)
        self._primary_key = self.prototype._primary_key
        self._deleted_flag = self.prototype._deleted_flag
        self._master_id = self.prototype._master_id
        self._master_rec_id = self.prototype._master_rec_id


    def do_internal_post(self):
        return {'success': True, 'id': None, 'message': '', 'detail_ids': None}

    def where_clause(self, query, db_module):
        master_id = query['__master_id']
        master_rec_id = query['__master_rec_id']
        if type(master_id) == int and type(master_rec_id) == int:
            result = super(Detail, self).where_clause(query, db_module)
            clause = '"%s"."%s"=%s AND "%s"."%s"=%s' % \
                (self.table_name.upper(), self._master_id, str(master_id),
                self.table_name.upper(), self._master_rec_id, str(master_rec_id))
            if result:
                result += ' AND ' + clause
            else:
                result = ' WHERE ' + clause
            return db_module.set_case(result)
        else:
            raise Exception, 'Invalid request parameter'

    def get_filters(self):
        return self.prototype.filters

    def get_reports_info(self):
        return []
