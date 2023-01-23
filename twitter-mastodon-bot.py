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

post_queue = queue.Queue()
DB_FILE = "twitter-mastodon-bot.db"
SLEEPTIME = 3 # minutes

def prettyPrintJSON(some_dic):
    print(json.dumps(some_dic, indent=4))


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
        print(f'DB: Updating {account} to: {last_id}')
        self.cursor.execute(f'UPDATE twitter SET last_id=\'{last_id}\' WHERE account=\'{account}\'')
        self.dbconn.commit()

class MyTwitter:
    '''
    Interfaces with Twitter accounts.
    Single account to login, but multiple accounts to monitor
    '''
    def __init__(self, config):
        print('Starting Twitter class')
        self.api = twitter.Api(
            consumer_key = config['twitter-conskey'],
            consumer_secret = config['twitter-conssec'],
            access_token_key = config['twitter-acctkkey'],
            access_token_secret = config['twitter-acctksec'],
            tweet_mode = "extended"
        )
        print('Authenticated at twitter.')

    def GetUserTimeline(self, **kwargs):
        '''
        Simple warpper for GetUserTimeline.
        '''
        return self.api.GetUserTimeline(**kwargs)

class MyMastodon:
    '''
    Interfaces with Mastodon account (single one).
    '''
    def __init__(self, config):
        print('Starting Mastodon class')
        self.api = Mastodon(
            access_token = config['mastodon-acctksec'],
            api_base_url = config['mastodon-instance']
        )
        print('Authenticated at Mastodon')
        print("About me:", self.api.me())

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
        print('Starting Bot class')
        self.parseArguments()
        self.readConfiguration()
        self.tw = MyTwitter(self.config)
        self.mst = MyMastodon(self.config)
        self.db = DataBase()

    def parseArguments(self):
        parser = argparse.ArgumentParser('Twitter to Mastodon bridge bot')
        parser.add_argument('--config', help='Configuration file')

        args = parser.parse_args()
        if args.config is None:
            raise Exception('Missing parameter --config')
        self.config = {'filename': args.config}

    def readConfiguration(self):
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
        print('configuration:', prettyPrintJSON(self.config))


    async def loop_twitter(self):
        while True:
            for account in  self.config['twitter-accounts']:
                    print('* checking:', account)
                    resp = self.tw.GetUserTimeline(screen_name=account)
                    print('raw:', resp)
                    for msg in resp:
                        print('msg:', msg)
                        last_id = self.db.getLastID(account)
                        if last_id > msg.id:
                            continue
                        print('ID:', msg.id)
                        print('ScreenName:', msg.user)
                        print('Created:', msg.created_at)
                        print('Text:', msg.text)
                        print('Full Text:', msg.full_text)
                        print('Media:', msg.media)
                        if msg.full_text:
                            full_text = msg.full_text
                        else:
                            full_text = msg.text
                        for entry in msg.urls:
                            print(" * Entry:", entry)
                            print(' * URL:', entry.url)
                            print(' * Expanded URL:', entry.expanded_url)
                            full_text = re.sub(entry.url, entry.expanded_url, full_text)
                        # if last character isn't new line, add it:
                        if full_text[-1] != '\n':
                            full_text += '\n'
                        # add hashtags
                        full_text += '\n'.join(self.config['hashtags'])
                        # inform about tests at this moment - to be removed later
                        full_text = '⚠️ Apenas um teste: ⚠️\n' + full_text
                        full_text += "\n🏁fim do teste🏁\n"
                        post_queue.put({
                            "account": account,
                            "id": msg.id,
                            "text": full_text
                        })
            time.sleep(SLEEPTIME * 60)

    async def loop_mastodon(self):
        while True:
            if post_queue.qsize() > 0:
                for obj in post_queue.get():
                    self.mst.status_post(status=obj["text"])
                    self.db.updateLastID(obj["account"], obj["id"])
            time.sleep(SLEEPTIME * 60)

    def mainloop(self):
        asyncio.run(self.loop_twitter())
        asyncio.run(self.loop_mastodon())

    def urlDestination(self, url):
        req = requests.get(url)
        if req.status_code != 200:
            return self.urlDestination(req.url)
        return self.urlDestination(req.url)

if __name__ == '__main__':
    bot = Bot()
    try:
        bot.mainloop()
    except KeyboardInterrupt:
        print('Stopping application')