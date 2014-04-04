#!/usr/bin/env python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""mozdef bot using KitnIRC."""
import logging
import threading
from datetime import datetime
import time
import kitnirc.client
import random
import pytz
from dateutil.parser import parse
import netaddr
import pygeoip
import os
from configlib import getConfig, OptionParser
import pika
import json
import select

greetz = ["mozdef bot in da house",
          "mozdef here..what's up",
          "mozdef has joined the room..no one panic",
          "mozdef bot here..nice to see everyone"]

retorts = ["why, why soo mean?",
           "someone got up on the wrong side of...",
           "yo momma arcsight",
           "gross arcsight..why you do that?",
           "fat chance arcsight",
           "arcsight you're such a show off"]

panics = ["don't panic",
          ".. a towel has immense psychological value",
          "..but in fact the message was this: 'So Long,"
          " and Thanks for All the Fish.'",
          "42",
          "What I need..is a strong drink and a peer group "
          "--Douglas Adams",
          "Eddies in the space-time continuum.",
          "segmentation fault..SEP"]

if os.path.isfile('quotes.txt'):
    quotes = open('quotes.txt').readlines()
else:
    quotes = ['nothing to say..add a quotes.txt file!']

colors = {
    'red': '\x034\x02',
    'normal': '\x03\x02',
    'blue': '\x032\x02',
    'green': '\x033\x02',
    'yellow': '\x038\x02',
}
keywords = {
    'INFORMATIONAL': colors['green'],
    'INFO': colors['green'],
    'WARNING': colors['yellow'],
    'CRITICAL': colors['red'],
    'mozdef': colors['blue'],
}


def colorify(data):
    for i in keywords:
            data = data.replace(i, keywords[i] + i + colors['normal'],
                1)
    return data


# http://code.activestate.com/recipes/576684-simple-threading-decorator/
def run_async(func):
    """
    run_async(func)
    function decorator, intended to make "func" run in a separate
    thread (asynchronously).
    Returns the created Thread object

    E.g.:
    @run_async
    def task1():
    do_something

    @run_async
    def task2():
    do_something_too

    t1 = task1()
    t2 = task2()
    ...
    t1.join()
    t2.join()
    """
    from threading import Thread
    from functools import wraps

    @wraps(func)
    def async_func(*args, **kwargs):
        func_hl = Thread(target=func, args=args, kwargs=kwargs)
        func_hl.start()
        return func_hl
    return async_func


def toUTC(suspectedDate, localTimeZone="US/Pacific"):
    '''make a UTC date out of almost anything'''
    utc = pytz.UTC
    objDate = None
    if type(suspectedDate) == str:
        objDate = parse(suspectedDate, fuzzy=True)
    elif type(suspectedDate) == datetime:
        objDate = suspectedDate

    if objDate.tzinfo is None:
        objDate = pytz.timezone(localTimeZone).localize(objDate)
        objDate = utc.normalize(objDate)
    else:
        objDate = utc.normalize(objDate)
    if objDate is not None:
        objDate = utc.normalize(objDate)

    return objDate


def getQuote():
    aquote = '{0} --Mos Def'.format(
        quotes[random.randint(0, len(quotes) - 1)].strip())
    return aquote


def isIP(ip):
    try:
        netaddr.IPNetwork(ip)
        return True
    except:
        return False


def ipLocation(ip):
    location = ""
    try:
        gi = pygeoip.GeoIP('GeoLiteCity.dat', pygeoip.MEMORY_CACHE)
        geoDict = gi.record_by_addr(str(netaddr.IPNetwork(ip)[0]))
        if geoDict is None:
            location = geoDict['country_name']
            if geoDict['country_code'] in ('US'):
                if geoDict['metro_code']:
                    location = location + '/{0}'.format(
                        geoDict['metro_code'])
    except Exception:
        location = ""
    return location


def formatAlert(jsonDictIn):
    # defaults
    severity = 'INFO'
    summary = ''
    category = ''
    if 'severity' in jsonDictIn.keys():
        severity = jsonDictIn['severity']
    if 'summary' in jsonDictIn.keys():
        summary = jsonDictIn['summary']
    if 'category' in jsonDictIn.keys():
        category = jsonDictIn['category']

    return colorify('{0}: {1} {2}'.format(
        severity,
        colors['blue'] + category + colors['normal'],
        summary))


class alertsListener(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self)
        # A flag to notify the thread that it should finish up and exit
        self.kill = False
        self.lastRunTime = datetime.now()
        self.client = client
        self.lastalerts = []
        self.openMQ()
        self.mqError = False
        self.connection = None
        self.channel = None

    def alertsCallback(self, ch, method, properties, bodyin):
        self.client.root_logger.debug(" [x]event %r:%r" % (
            method.routing_key, bodyin))
        try:
            jbody = json.loads(bodyin)
            self.client.msg(options.alertircchannel, formatAlert(jbody))

        except Exception as e:
            self.client.root_logger.error('Exception on message queue'
                                          'callback {0}'.format(e))

    @run_async
    def openMQ(self):
        try:
            if self.connection is None and not self.kill:
                self.mqError = False
                self.connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=options.mqserver,
                        heartbeat_interval=10))
                # give the irc client visibility to our connection state.
                self.client.mqconnection = self.connection
                self.client.root_logger.info('opening message queue channel')
                if self.channel is None:
                    self.channel = self.connection.channel()
                    self.channel.exchange_declare(
                        exchange=options.alertexchange,
                        type='topic')
                    result = self.channel.queue_declare(exclusive=False)
                    queue_name = result.method.queue
                    self.channel.queue_bind(
                        exchange=options.alertexchange,
                        queue=queue_name,
                        routing_key=options.alertqueue)

                self.client.root_logger.info(
                    'INFO consuming message queue {0}'.format(
                        options.alertqueue))
                self.client.msg(options.alertircchannel,
                    'consuming message queue {0}'.format(
                        options.alertqueue))
                self.channel.basic_consume(
                    self.alertsCallback, queue=queue_name, no_ack=True)
                self.channel.start_consuming()
        except pika.exceptions.ConnectionClosed as e:
            self.client.root_logger.error("MQ Connection closed {0}".format(e))
            self.client.msg(options.alertircchannel,
                "ERROR: Message queue is closed. No alerts for you")
            self.mqError = True
            try:
                self.connection = None
                self.channel = None
            except:
                pass
        except AttributeError:
            pass
        except select.error:
            pass
        except Exception as e:
            self.mqError = True
            self.client.root_logger.error(
                "Exception {0} while processing alerts"
                "message queue.".format(type(e)))
            self.client.msg(options.alertircchannel,
                "ERROR: Exception {0} while processing alerts"
                " message queue".format(e))

            try:
                if self.connection is not None:
                    self.connection.close()
            except:
                pass
            finally:
                self.connection = None
                self.channel = None

    def run(self):
        while not self.kill:
            try:
                self.client.root_logger.debug('checking mq connections')
                if (self.connection is None
                 or self.mqError or not self.connection.is_open):
                    self.openMQ()
                    self.client.root_logger.info('opening mq connection')
                    time.sleep(10)

            except Exception as e:
                self.client.root_logger.error(
                    "Exception {0} while polling alerts message queue"
                    " health.".format(e))
            time.sleep(int(10))


class alertsWorker(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self)
        # A flag to notify the thread that it should finish up and exit
        self.kill = False
        self.lastRunTime = datetime.now()
        self.client = client
        self.lastalerts = []

    def run(self):
        while not self.kill:
            self.checkAlerts()

    def checkAlerts(self):
        try:
            if (datetime.now() - self.lastRunTime).seconds > int(10):

                self.client.root_logger.debug("checking for alerts")
                # self.client.msg("#mzdf","checking for alerts...")
                # analert='{0}: {1}'.format(datetime.now()
                # random.randint(0,100))
                esresults = esSearchFail2ban(toUTC(self.lastRunTime))
                # esresults=esSearchFail2ban(toUTC("Nov 18"))
                for analert in esresults:
                    if analert not in self.lastalerts:
                        self.lastalerts.append(analert)
                        self.client.msg(alertChannel, analert)
                        self.client.root_logger.debug(
                            'sent {0}'.format(analert))
                        if len(self.lastalerts) > 100:
                            self.client.root_logger.debug(
                                'removing:{0}'.format(self.lastalerts[0]))
                            self.lastalerts.remove(self.lastalerts[0])
                self.lastRunTime = datetime.now()
            else:
                # self.client.root_logger.debug("not alert time..sleeping")
                time.sleep(int(5))
        except Exception as e:
            self.client.root_logger.error(
                "Exception {0} while checking for alerts.".format(e))


class mozdefBot():

    def __init__(self, ):

        # Logging initialization
        self.log_handler = logging.StreamHandler()
        self.log_formatter = logging.Formatter("%(asctime)s %(message)s")
        self.log_handler.setFormatter(self.log_formatter)

        self.root_logger = logging.getLogger()
        self.root_logger.addHandler(self.log_handler)
        self.root_logger.setLevel(logging.INFO)

        self.client = kitnirc.client.Client(options.host, options.port)
        self.client.root_logger = self.root_logger
        self.client.connect(
            nick=options.nick,
            username=options.username or options.nick,
            realname=options.realname or options.username or options.nick,
            password=options.password,
            ssl=True
        )
        self.threads = []
        self.mqconnection = None

    def run(self):
        try:
            @self.client.handle('WELCOME')
            def join_channels(client, *params):
                if not options.join:
                    return
                for chan in options.join.split(","):
                    if chan in options.channelkeys.keys():
                        client.join(chan, options.channelkeys[chan])
                    else:
                        client.join(chan)
                t = alertsListener(self.client)
                self.threads.append(t)
                t.start()

            @self.client.handle('LINE')
            def line_handler(client, *params):
                try:
                    self.root_logger.debug('linegot:' + line)
                except AttributeError:
                    # catch error in kitnrc : chan.remove(actor)
                    # where channel object has no attribute remove
                    pass

            @self.client.handle('PRIVMSG')
            def priv_handler(client, actor, recipient, message):
                self.root_logger.debug(
                    'privmsggot:' + message + ' from ' + actor)
                if 'ArcSight' in actor or 'jeff' in actor:
                    if 'BANG' in message:
                        self.client.msg(recipient,
                            random.choice(retorts))
                if "!help" in message:
                    self.client.msg(recipient,
                        "I just send alerts and taunt arcsight.."
                        "for now..but try these:")
                    self.client.msg(recipient,
                        "!quote  --get a quote from my buddy Mos Def")
                    self.client.msg(recipient,
                        "!panic  --panic (or not )")
                    self.client.msg(recipient,
                        "!ipinfo --do a geoip lookup on an ip address")
                if "!quote" in message:
                    self.client.msg(recipient, getQuote())
                if "!panic" in message:
                    self.client.msg(recipient, random.choice(panics))
                if "!ipinfo" in message:
                    for i in message.split():
                        if isIP(i):
                            ip = netaddr.IPNetwork(i)[0]
                            if (not ip.is_loopback()
                             and not ip.is_private()
                             and not ip.is_reserved()):
                                self.client.msg(recipient,
                                    "{0} location: {1}".format(
                                        i, ipLocation(i)))
                            else:
                                self.client.msg(recipient,
                                    "{0}: hrm..loopback? private"
                                    " ip?".format(i))

            @self.client.handle('JOIN')
            def join_handler(client, user, channel, *params):
                self.root_logger.debug('%r' % channel)
                if user.nick == options.nick:
                    self.client.msg(channel, colorify(random.choice(greetz)))
            self.client.run()

        except KeyboardInterrupt:
            for t in self.threads:
                t.kill = True
            self.client.disconnect()
            if self.client.mqconnection is not None:
                try:
                    self.client.mqconnection.close()
                except:
                    pass
        except Exception as e:
            self.client.root_logger.error('bot error..quitting {0}'.format(e))
            for t in self.threads:
                t.kill = True
            self.client.disconnect()
            if self.client.mqconnection is not None:
                self.client.mqconnection.close()


def initConfig():
    # initialize config options
    # sets defaults or overrides from config file.
    options.host = getConfig('host',
        'irc.somewhere.com', options.configfile)
    options.nick = getConfig('nick',
        'mozdefnick', options.configfile)
    options.port = getConfig('port', 6697, options.configfile)
    options.username = getConfig('username', 'username',
        options.configfile)
    options.realname = getConfig('realname', 'realname',
        options.configfile)
    options.password = getConfig('password', '', options.configfile)
    options.join = getConfig('join', '#mzdf', options.configfile)
    options.esserver = getConfig('esserver', 'localhost',
        options.configfile)
    options.mqserver = getConfig('mqserver', 'localhost',
        options.configfile)
    options.alertqueue = getConfig('alertqueue', 'mozdef.alert',
        options.configfile)
    options.alertexchange = getConfig('alertexchange', 'alerts',
        options.configfile)
    options.alertircchannel = getConfig('alertircchannel', '',
        options.configfile)
    options.channelkeys = json.loads(
        getConfig('channelkeys', '{"#somechannel": "somekey"}',
            options.configfile))

    if options.alertircchannel == '':
        options.alertircchannel = options.join

if __name__ == "__main__":
    parser = OptionParser()
    parser.add_option(
        "-c",
        dest='configfile',
        default='',
        help="configuration file to use"
    )
    (options, args) = parser.parse_args()
    initConfig()

    thebot = mozdefBot()
    thebot.run()

# vim: set ts=4 sts=4 sw=4 et:
