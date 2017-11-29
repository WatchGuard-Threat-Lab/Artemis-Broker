#!/usr/bin/python

import pymongo
import sys
import config

client = pymongo.MongoClient(host=config.MONGOHOST,port=config.MONGOPORT)
for doc in client.hpfeeds.auth_key.find():
    print doc


