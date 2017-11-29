#!/usr/bin/python

import pymongo
import config
import sys

def handle_list(arg):
    if arg:
        return arg.split(",")
    else:
        return []

if len(sys.argv) < 3:
    print >> sys.stderr, "Usage: %s <ident> <secret>"%sys.argv[0]
    sys.exit(1)

ident = sys.argv[1]
secret = sys.argv[2]
publish = ['NONE']
subscribe = ['thug.files','thug.events','artemis.parsed']

rec = {
    "identifier": ident,
    "secret": secret,
    "publish": publish,
    "subscribe":subscribe
}

client = pymongo.MongoClient(host=config.MONGOHOST,port=config.MONGOPORT)
res = client.hpfeeds.auth_key.update({"identifier": ident}, {"$set": rec}, upsert=True)
client.fsync()
client.close()

if res['updatedExisting']:
    print "updated %s"%rec
else:
    print "inserted %s"%(rec)

