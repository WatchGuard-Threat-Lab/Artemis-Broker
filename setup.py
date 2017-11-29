import sys

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

install_requires = [
    'cffi',
    'pyopenssl==0.14',
    'pymongo',
    'greenlet',
    'gevent',
    'python-daemon',
]

dependancy_links = [
    '-e git+https://github.com/rep/evnet.git#egg=evnet-dev',
]

config = {
    'name':'artemis-broker',
    'description': 'HPFeeds broker for Artemis honeynet',
    'url':'https://github.com/WatchGuard-Threat-Lab/artemis-broker',
    'author':'Zed A. Shaw',
    'maintainer':'Marc Laliberte',
    'version':'1.0.0',
    'install_requires': install_requires,
    'dependancy_links': dependancy_links,
    'package_dir':{'':'lib'},
    'license':'GPLv3',
    'scripts':['cli/hpfeeds-client'],
}

setup(**config)
