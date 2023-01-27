#! /usr/bin/env python3
import time
import sys
import argparse
import configparser
import json
from mastodon import Mastodon
import twitter
import sqlite3
import re
import requests
import asyncio
import queue
import logging
import random

post_queue = queue.Queue(10)
DB_FILE = "twitter-mastodon-bot.db"
SLEEPTIME = 5 # minutes

logging.basicConfig()
logging.root.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)
logger.setLevel('INFO')

def prettyJSON(some_dic):
    return json.dumps(some_dic, sort_keys=True, indent=4)

def urlDestination(url):
    logger.debug(f'url received: {url}')
    req = requests.get(url)
    if req.status_code != 200:
        logger.debug(f'url got {req.status_code} - trying again')
        return urlDestination(req.url)
    logger.debug(f'url got 200 - continue and returning: {req.url}')
    return req.url


class DataBase:
    def __init__(self):
        self.dbconn = sqlite3.connect(DB_FILE)
        self.cursor = self.dbconn.cursor()
        self.initialize()

    def initialize(self):
        try:
            self.createDB()
        except sqlite3.OperationalError:
            pass

    def createDB(self):
        self.cursor.execute('CREATE TABLE twitter(account, last_id)')

    def getLastID(self, account):
        self.cursor.execute(f'SELECT last_id from twitter WHERE account=\'{account}\'')
        result = self.cursor.fetchone()
        if result is None:
            self.cursor.execute(f'INSERT INTO twitter VALUES (\'{account}\', \'0\')')
            self.dbconn.commit()
            return 0
        return int(result[0])

    def updateLastID(self, account, last_id):
        logger.debug(f'DB: Updating {account} to: {last_id}')
        self.cursor.execute(f'UPDATE twitter SET last_id=\'{last_id}\' WHERE account=\'{account}\'')
        self.dbconn.commit()

class MyTwitter:
    '''
    Interfaces with Twitter accounts.
    Single account to login, but multiple accounts to monitor
    '''
    def __init__(self, config):
        logger.debug('Starting Twitter class')
        self.api = twitter.Api(
            consumer_key = config['twitter-conskey'],
            consumer_secret = config['twitter-conssec'],
            access_token_key = config['twitter-acctkkey'],
            access_token_secret = config['twitter-acctksec'],
            tweet_mode = "extended"
        )
        logger.info('Authenticated at twitter.')

    def GetUserTimeline(self, **kwargs):
        '''
        Simple warpper for GetUserTimeline.
        '''
        return self.api.GetUserTimeline(**kwargs)

    def UnTwittefy(self, message):
        '''
        Replace @name by @name@twitter.com to match mastodon format.
        '''
        wordlist = []
        for c in re.finditer('@', message):
            char_start = c.span()[0]
            # find where it ends
            counter = 0
            char_stop = 0
            for w in message[char_start:]:
                if re.match(' |\.|,|;|:|\)|\|\'|\"|\n', w):
                    char_stop = char_start + counter
                    break
                counter += 1
            if char_stop == 0:
                # it reached the end of message and couldn't find it.
                # so it is the total nr by counter
                char_stop = char_start + counter
            wordlist.append(message[char_start:char_stop])

        for word in wordlist:
            message = re.sub(word, f'{word}@twitter.com', message)
        return message

    def UnTCOfy(self, message):
        '''
        Replace all https://t.co references by full link.
        '''
        linklist = []
        # note: this is a copy from UnTwittefy, so there is room for
        #        refactoring
        for c in re.finditer('https://t.co/', message):
            char_start = c.span()[0]
            header_size = len('https://t.co/')
            counter = header_size
            char_stop = 0
            for w in message[char_start + header_size:]:
                if re.match(' |\.|,|;|\)|\|\'|\"|\n', w):
                    char_stop = char_start + counter
                    break
                counter += 1
            if char_stop == header_size:
                # it reached the end of message and couldn't find it.
                # so it is the total nr by counter
                char_stop = char_start + counter
            if len(message[char_start:char_stop]) > header_size:
                linklist.append(message[char_start:char_stop])


        for link in linklist:
            logger.debug(f'link: {link}')
            message = re.sub(link, urlDestination(link) , message)
        return message


class MyMastodon:
    '''
    Interfaces with Mastodon account (single one).
    '''
    def __init__(self, config):
        logger.debug('Starting Mastodon class')
        self.api = Mastodon(
            access_token = config['mastodon-acctksec'],
            api_base_url = config['mastodon-instance']
        )
        logger.info('Authenticated at Mastodon')
        logger.debug("About me:", self.api.me())

    def status_post(self, **kwargs):
        '''
        Simple wrapper for status_post.
        '''
        return self.api.status_post(**kwargs)

class Bot:
    '''
    Start and control bot.
    '''
    def __init__(self):
        logger.debug('Starting Bot class')
        self.parseArguments()
        self.readConfiguration()
        self.tw = MyTwitter(self.config)
        self.mst = MyMastodon(self.config)
        self.db = DataBase()

    def parseArguments(self):
        parser = argparse.ArgumentParser('Twitter to Mastodon bridge bot')
        parser.add_argument('--config', help='Configuration file')
        parser.add_argument('--loglevel', help='Logging level (default: INFO)')

        args = parser.parse_args()
        if args.config is None:
            raise Exception('Missing parameter --config')
        self.config = {'filename' : args.config}
        if args.loglevel:
            logger.setLevel(args.loglevel.upper())
            logger.debug('logging level set to: ' + args.loglevel.upper())

    def readConfiguration(self):
        logger.info("Reading configuration")
        cfg = configparser.ConfigParser()
        cfg.read(self.config['filename'])
        # authentication
        self.config['twitter-conskey'] = cfg.get('TWITTER', 'consumer_key')
        self.config['twitter-conssec'] = cfg.get('TWITTER', 'consumer_secret')
        self.config['twitter-acctkkey'] = cfg.get('TWITTER', 'access_token_key')
        self.config['twitter-acctksec'] = cfg.get('TWITTER', 'access_token_secret')
        self.config['mastodon-acctksec'] = cfg.get('MASTODON', 'access_token')
        self.config['mastodon-instance'] = cfg.get('MASTODON', 'instance')
        # extra
        self.config['twitter-accounts'] = cfg.get('TWITTER', 'accounts').split(",")
        self.config['hashtags'] = []
        try:
            all_hashtags = cfg.get('MASTODON', 'hashtags')
            for hashtag in all_hashtags.split(","):
                # remove spaces
                hashtag = re.sub(" ", "", hashtag)
                self.config['hashtags'].append(f'#{hashtag}')
        except configparser.NoOptionError:
            pass
        result = prettyJSON(self.config)
        logger.debug('configuration: ' + result)

    async def loop_twitter(self):
        while True:
            logger.debug("inside twitter loop")
            logger.debug(f"twitter queue size {post_queue.qsize()}")
            # Note: the ids come in decreasing order, like FIFO order.
            #       So better approach is to read all them in a dictionary
            #       structure and order by id later to decide or not to publish.
            twitter_content = {}
            for account in  self.config['twitter-accounts']:
                    logger.debug(f'checking: {account}')
                    last_id = self.db.getLastID(account)
                    if last_id == 0:
                        resp = self.tw.GetUserTimeline(screen_name=account)
                    else:
                        resp = self.tw.GetUserTimeline(screen_name=account, since_id=last_id)
                    # logger.debug('raw:', resp)
                    logger.info(f"Found {len(resp)} new tweets for {account}")
                    for msg in resp:
                        logger.debug(f'twitter msg: {msg}')
                        # do this last_id is needed here?
                        logger.debug(f'ID: {msg.id}')
                        logger.debug(f'ScreenName: {msg.user}')
                        logger.debug(f'Created: {msg.created_at}')
                        logger.debug(f'Text: {msg.text}')
                        logger.debug(f'Full Text: {msg.full_text}')
                        logger.debug(f'Media: {msg.media}')
                        if msg.full_text:
                            full_text = msg.full_text
                        else:
                            full_text = msg.text
                        # fix twitter references
                        full_text = self.tw.UnTwittefy(full_text)
                        full_text = self.tw.UnTCOfy(full_text)
                        for entry in msg.urls:
                            logger.debug(f"Full URL entry: {entry}")
                            logger.debug(f'URL: {entry.url}')
                            logger.debug(f'Expanded URL: {entry.expanded_url}')
                            full_text = re.sub(entry.url, entry.expanded_url, full_text)
                        # if last character isn't new line, add it:
                        if full_text[-1] != '\n':
                            full_text += '\n'
                        # add hashtags
                        full_text += '\n\n'.join(self.config['hashtags'])
                        # inform about tests at this moment - to be removed later
                        full_text = '⚠️ Apenas um teste: ⚠️\n\n' + full_text
                        if not account in twitter_content:
                            twitter_content[account] = {}
                        twitter_content[account][msg.id] = full_text

            # handling the messages here
            for account in twitter_content.keys():
                for msg_id in sorted(twitter_content[account].keys(), reverse=True):
                    last_id = self.db.getLastID(account)
                    if last_id > msg_id:
                        logger.debug(f'message with id {msg_id} was already sent')
                        continue
                    while post_queue.full():
                        naptime = random.randint(0, 60)
                        logger.debug(f'queue is full on twitter side - waiting {sleeptime} s')
                        await asyncio.sleep(naptime)
                    post_queue.put({
                        "account": account,
                        "id": msg_id,
                        "text" : twitter_content[account][msg_id]
                    })
                    logger.debug(f'queue size at twitter loop: {post_queue.qsize()}')
                    # to give time to see messages in the debug log righ now - to disable it later
                    await asyncio.sleep(30)

            await asyncio.sleep(SLEEPTIME * 60)
            logger.debug('restarting twitter loop')

    async def loop_mastodon(self):
        # start delayed
        await asyncio.sleep(10)
        logger.info('starting mastodon queue monitoring')
        while True:
            logger.debug("inside mastodon loop")
            logger.debug(f"mastodon queue size {post_queue.qsize()}")
            while post_queue.qsize():
                logger.debug(f"queue size at mastodon loop: {post_queue.qsize()}")
                obj = post_queue.get()
                logger.debug(f"queue data at mastodon: {obj}")
                logger.info(f'posting account={obj["account"]}, msg_id={obj["id"]}')
                self.mst.status_post(status=obj["text"])
                self.db.updateLastID(obj["account"], obj["id"])
            await asyncio.sleep(SLEEPTIME * 60)
            logger.debug('restarting mastodon loop')

    async def simple_loop_twitter(self):
        '''
        Just simple asyncio loop to fix queues, etc for twitter.
        '''
        logger.debug('Started simple loop twitter')
        counter = 0
        await asyncio.sleep(10)
        while True:
            logger.debug(f'twitter queue size: {post_queue.qsize()}')
            post_queue.put_nowait({'size': counter})
            counter += 1
            await asyncio.sleep(random.randint(0,10))

    async def simple_loop_mastodon(self):
        '''
        Just simple asyncio loop to fix queues, etc for mastodon.
        '''
        logger.debug('Started simple loop mastodon')
        while True:
            logger.debug(f'mastodon queue size: {post_queue.qsize()}')
            while post_queue.qsize() > 0:
                data = post_queue.get_nowait()
                logger.debug(f'* mastodon data got: {data}')
            await asyncio.sleep(random.randint(0, 10))
        await asyncio.sleep(3)

    async def mainloop(self):
        logger.info("starting mainloop - waiting for news")
        async with asyncio.TaskGroup() as tg:
            task1 = tg.create_task(self.loop_mastodon())
            task2 = tg.create_task(self.loop_twitter())
        logger.info("ending processing messages")

if __name__ == '__main__':
    bot = Bot()
    try:
        asyncio.run(bot.mainloop())
    except KeyboardInterrupt:
        logger.warning('Stopping application')