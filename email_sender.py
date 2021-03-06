#! /usr/bin/env python

import sys
import os
import time
import re
import codecs
import yaml
import json
import logging
import sqlite3
from daemon import runner
import boto.ses

with open(u'%s/conf.yaml' % os.path.dirname(__file__), u'r') as f:
    conf = yaml.safe_load(f)

db_path = conf[u'db_path']
table_name = conf[u'table_name']
magic_string = conf[u'magic_string']
stdin_path = conf[u'stdin_path']
stdout_path = conf[u'stdout_path']
stderr_path = conf[u'stderr_path']
pidfile_path = conf[u'pidfile_path']
pidfile_timeout = conf[u'pidfile_timeout']
log_file = conf[u'log_file']
daemon_interval = conf[u'daemon_interval']
do_test = conf[u'do_test']
result_info_len = conf[u'result_info_len']

def test_send_email(source, subject, body, to_addresses, format, reply_addresses, return_path, text_body=None, html_body=None):
    logging.info("**********************test************************")
    logging.info(u'test: %s %s %s %s %s %s %s %s %s' % (unicode(source), unicode(subject), unicode(body), \
                     unicode(to_addresses), unicode(format), unicode(reply_addresses), unicode(return_path), \
                     unicode(text_body), unicode(html_body)))
    time.sleep(0.2)

default_pseudo_send_count = 3
default_pattern_begin = u'\{\{'
default_pattern_end = u'\}\}'
default_update_interval = 100
default_ignore_mismatch = 0
def batch_send_email(sender_file_name, subject_file_name, emailbody_file_name, dest_file_name, actualsend, update_count):
    with codecs.open(sender_file_name, u'r', u'utf-8') as f:
        conf1 = yaml.safe_load(f)

    if u'aws_access_key_id' not in conf1:
        return u'no aws_access_key_id'
    aws_access_key_id = conf1[u'aws_access_key_id']

    if u'aws_secret_access_key' not in conf1:
        return u'no aws_secret_access_key'
    aws_secret_access_key = conf1[u'aws_secret_access_key']

    if u'region' not in conf1:
        return u'no region'
    region = conf1[u'region']

    if u'email_address' not in conf1:
        return u'no email_address'
    source = conf1[u'email_address']

    if u'reply_addresses' in conf1:
        reply_addresses = conf1[u'reply_addresses']
    else:
        reply_addresses = source

    if u'return_path' in conf1:
        return_path = conf1[u'return_path']
    else:
        return_path = reply_addresses

    if u'pseudo_send_count' in conf1:
        pseudo_send_count = conf1[u'pseudo_send_count']
    else:
        pseudo_send_count = default_pseudo_send_count

    if u'pattern_begin' in conf1:
        pattern_begin = conf1[u'pattern_begin']
    else:
        pattern_begin = default_pattern_begin

    if u'pattern_end' in conf1:
        pattern_end = conf1[u'pattern_end']
    else:
        pattern_end = default_pattern_end

    if u'update_interval' in conf1:
        update_interval = conf1[u'update_interval']
    else:
        update_interval = default_update_interval

    if u'ignore_mismatch' in conf1:
        ignore_mismatch = conf1[u'ignore_mismatch']
    else:
        ignore_mismatch = default_ignore_mismatch

    with codecs.open(subject_file_name, u'r', u'utf-8') as f:
        subject = f.read()

    with codecs.open(emailbody_file_name, u'r', u'utf-8') as f:
        emailbody = f.read()

    if emailbody_file_name[-5:] == u'.html':
        format = u'html'
    elif emailbody_file_name[-4:] == u'.txt':
        format = u'text'
    else:
        return u'unsupport format'
    conn = boto.ses.connect_to_region(region, aws_access_key_id = aws_access_key_id, aws_secret_access_key = aws_secret_access_key)
    ret = []
    send_count = 0
    f = codecs.open(dest_file_name, u'r', u'utf-8')
    line_number = 0
    for eachline in f:
        line_number += 1
        tmpbody = emailbody
        items = eachline.split(u',')
        if len(items) < 1:
            continue
        to_addresses = items[0].strip()
        if not to_addresses:
            continue
        count = 1
        mismatch = False
        for item in items[1:]:
            item = item.strip()
            m = u'%s%s%s' % (pattern_begin, count, pattern_end)
            p = re.compile(m)
            tmpbody, n = re.subn(p, item, tmpbody)
            if n == 0:
                info = u'mismatch %s %s %s' % (to_addresses, item, count)
                ret.append(info)
                mismatch = True
            count += 1
        if mismatch and (not ignore_mismatch):
            continue
        if do_test:
            send_email = test_send_email
        else:
            send_email = conn.send_email
        try:
            if actualsend:
                if format == u'html':
                    send_email(source, subject, None, \
                                   to_addresses, format=format, reply_addresses=reply_addresses, return_path=return_path, html_body=tmpbody)
                else:
                    send_email(source, subject, None, \
                                   to_addresses, format=format, reply_addresses=reply_addresses, return_path=return_path, text_body=tmpbody)
            else:
                if send_count < pseudo_send_count:
                    pseudo_subject = u'%s [%s]' % (subject, to_addresses)
                    if format == u'html':
                        send_email(source, pseudo_subject, None, \
                                       source, format=format, reply_addresses=reply_addresses, return_path=return_path, html_body=tmpbody)
                    else:
                        send_email(source, pseudo_subject, None, \
                                       source, format=format, reply_addresses=reply_addresses, return_path=return_path, text_body=tmpbody)
        except Exception, e:
            # msg = u'line number: %d\n%s' % (line_number, unicode(e))
            msg = u'line number: %d' % line_number
            ret.append(msg)
        else:
            send_count += 1
            if (send_count % update_interval) == 0:
                update_count(send_count)
    update_count(send_count)
    ret.append(u'done, send %d' % send_count)
    return ret

class EmailSender():
    def __init__(self, stdin_path, stdout_path, stderr_path, pidfile_path, pidfile_timeout, \
                     db_path, table_name, magic_string, log_file, daemon_interval, result_info_len):
        self.stdin_path = stdin_path
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.pidfile_path = pidfile_path
        self.pidfile_timeout = pidfile_timeout
        self.db_path = db_path
        self.table_name = table_name
        self.magic_string = magic_string
        self.log_file = log_file
        self.daemon_interval = daemon_interval
        self.result_info_len = result_info_len
    def run(self):
        format = '%(asctime)s - %(filename)s:%(lineno)s - %(name)s - %(message)s'
        datefmt='%Y-%m-%d %H:%M:%S'
        logging.basicConfig(filename = self.log_file, level = logging.INFO, format=format, datefmt=datefmt)
        cx = sqlite3.connect(self.db_path)
        cu = cx.cursor()
        cmd = u'create table if not exists %s (' % self.table_name + \
            u'magic_string varchar(10) primary key,' + \
            u'sender_file_name varchar(255),' + \
            u'subject_file_name varchar(255),' + \
            u'emailbody_file_name varchar(255),' + \
            u'dest_file_name varchar(255),' + \
            u'actualsend int,' + \
            u'status varchar(10),' + \
            u'complete_count int,' + \
            u'result_info varchar(%d)' % self.result_info_len + \
            u')'
        cu.execute(cmd)
        cu.close()
        logging.info(u'come to loop')
        while True:
            cu = cx.cursor()
            cmd = u'select * from %s where magic_string="%s"' % (self.table_name, self.magic_string)
            cu.execute(cmd)
            ret = cu.fetchone()
            if not ret:
                logging.debug(u'empty, continue to sleep')
                time.sleep(self.daemon_interval)
                continue
            (magic_string, sender_file_name, subject_file_name, emailbody_file_name, \
                 dest_file_name, actualsend, status, complete_count, result_info) = ret
            if status == u'done':
                logging.debug(u'done, continue to sleep')
                time.sleep(self.daemon_interval)
                continue
            logging.info(u'task: %s' % unicode(ret))
            try:
                info = batch_send_email(sender_file_name, subject_file_name, emailbody_file_name, \
                                     dest_file_name, actualsend, self.update_count)
            except Exception, e:
                info = e
            info = unicode(info)
            if len(info) >= self.result_info_len:
                info = info[0:self.result_info_len]
            info = info.replace(u'"',u'')
            cmd = u'''update %s set status="done", result_info="%s" where magic_string = "%s"''' % \
                (self.table_name, info, self.magic_string)
            logging.info(u'set to done, cmd: %s' % cmd)
            cu.execute(cmd)
            cx.commit()
            cu.close()
            time.sleep(3)
    def update_count(self, count):
        cx = sqlite3.connect(self.db_path)
        cu = cx.cursor()
        cmd = u'update %s set complete_count=%d where magic_string="%s"' % \
            (self.table_name, count, self.magic_string)
        cu.execute(cmd)
        cx.commit()
        cu.close()
        cx.close()

email_sender = EmailSender(stdin_path, stdout_path, stderr_path, pidfile_path, pidfile_timeout, \
                               db_path, table_name, magic_string, log_file, daemon_interval, result_info_len)

daemon_runner = runner.DaemonRunner(email_sender)
daemon_runner.do_action()
