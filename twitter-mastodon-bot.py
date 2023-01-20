#! /usr/bin/env python3
import time
import sys
import argparse
import configparser
import json
from mastodon import Mastodon
import twitter
import sqlite3

DB_FILE = "twitter-mastodon-bot.db"

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
            access_token_secret = config['twitter-acctksec']
        )
        print('Authenticated at twitter.')


class MyMastodon:
    '''
    Interfaces with Mastodon account (single one).
    '''
    def __init__(self, config):
        print('Starting Mastodon class')
        self.api = Mastodon(
            access_token = config['mastodon-clisec'],
            api_base_url = config['mastodon-instance']
        )
        print('Authenticated at Mastodon')

class Bot:
    '''
    Start and control bot.
    '''
    def __init__(self):
        print('Starting Bot class')
        self.parseArguments()
        self.readConfiguration()
        self.tw = MyTwitter(self.config)
        self.toot = MyMastodon(self.config)
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
        self.config['twitter-conskey'] = cfg.get('TWITTER', 'consumer_key')
        self.config['twitter-conssec'] = cfg.get('TWITTER', 'consumer_secret')
        self.config['twitter-acctkkey'] = cfg.get('TWITTER', 'access_token_key')
        self.config['twitter-acctksec'] = cfg.get('TWITTER', 'access_token_secret')
        self.config['mastodon-clisec'] = cfg.get('MASTODON', 'client_secret')
        self.config['mastodon-instance'] = cfg.get('MASTODON', 'instance')
        self.config['twitter-accounts'] = cfg.get('TWITTER', 'accounts').split(",")
        print('configuration:', prettyPrintJSON(self.config))


    def mainloop(self):
        while True:
            print('keep running')
            for account in self.config['twitter-accounts']:
                print('* checking:', account)
                resp = self.tw.api.GetUserTimeline(screen_name=account)
                print('raw:', resp)
                # Status(ID=1616424976193622023, ScreenName=GloboNews, Created=Fri Jan 20 13:18:40 +0000 2023, Text='Presidente Lula vai se reunir com comandantes das Forças Armadas, nesta sexta (20). Encontro mira fim da crise com… https://t.co/SAakcoHt7S')
                for msg in resp:
                    #print('msg:', msg)
                    #print(dir(msg))
                    last_id = self.db.getLastID(account)
                    if last_id > msg.id:
                        continue
                    print('ID:', msg.id)
                    print('ScreenName:', msg.user)
                    print('Created:', msg.created_at)
                    print('Text:', msg.text)
                    print('Full Text:', msg.full_text)
                    print('Media:', msg.media)
                    self.db.updateLastID(account, msg.id)
                    print('###################################')
            break
            time.sleep(3)
if __name__ == '__main__':
    bot = Bot()
    try:
        bot.mainloop()
    except KeyboardInterrupt:
        print('Stopping application')