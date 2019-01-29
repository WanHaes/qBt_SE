# VERSION: 1.3
# AUTHORS: imDMG [imdmgg@gmail.com]

# Kinozal.tv search engine plugin for qBittorrent

import tempfile
import os
import logging
import json
# import re
import time

from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from novaprinter import prettyPrinter

# setup logging into qBittorrent/logs
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                    datefmt='%m-%d %H:%M',
                    filename=os.path.abspath(os.path.join(os.path.dirname(__file__), '../../logs', 'kinozal.log')),
                    filemode='w')

# benchmark
start_time = time.time()


class kinozal(object):
    name = 'Kinozal'
    url = 'http://kinozal.tv'
    supported_categories = {'all': '0',
                            'movies': '1002',
                            'tv': '1001',
                            'music': '1004',
                            'games': '23',
                            'anime': '20',
                            'software': '32'}

    # getting config from kinozal.json
    config = None
    try:
        # try to load user data from file
        with open(os.path.abspath(os.path.join(os.path.dirname(__file__), 'kinozal.json'))) as f:
            config: dict = json.load(f)
    except OSError as e:
        # file not found
        logging.error(e)
        raise e

    def __init__(self):
        # establish connection
        #
        # make cookie
        cj = CookieJar()
        self.session = build_opener(HTTPCookieProcessor(cj))

        # avoid endless waiting
        self.blocked = False

        # add proxy handler if needed
        if self.config['proxy'] and any(self.config['proxies'].keys()):
            self.session.add_handler(ProxyHandler(self.config['proxies']))

        # change user-agent
        self.session.addheaders.pop()
        self.session.addheaders.append(('User-Agent', self.config['ua']))

        form_data = {"username": self.config['username'], "password": self.config['password']}
        # so we first encode keys to cp1251 then do default decode whole string
        data_encoded = urlencode({k: v.encode('cp1251') for k, v in form_data.items()}).encode()

        self._catch_error_request(self.url + '/takelogin.php', data_encoded)
        if not self.blocked:
            if 'uid' not in [cookie.name for cookie in cj]:
                logging.warning("we not authorized, please check your credentials")
            else:
                logging.info('We successfully authorized')

    class WorstParser(HTMLParser):
        def __init__(self, url=''):
            HTMLParser.__init__(self)
            self.url = url
            self.torrent = {'link': '',
                            'name': '',
                            'size': '',
                            'seeds': '',
                            'leech': '',
                            'desc_link': '', }

            # we need a page markup to know when stop and collect data,
            # because available methods, in this class, do not communicate each other
            # as a result, we make markup to transfer information
            # from one method to another, along a chain
            #
            # markup on result table
            self.result_table = False  # table with results is found
            self.torrent_row = False  # found torrent row for collect data
            self.index_td = 0  # td counter in torrent row
            self.write = None  # trigger to detecting when to collect data

            # markup pagination
            self.paginator = False  # found more pages in result
            self.pages = 0  # page counter

            self.found_torrents = 0

        def handle_starttag(self, tag, attrs):
            # search result table by class t_peer
            if tag == 'table':
                for name, value in attrs:
                    if name == 'class' and 't_peer' in value:
                        self.result_table = True

            # search for torrent row by class bg
            if self.result_table and tag == 'tr':
                for name, value in attrs:
                    if name == 'class' and 'bg' in value:
                        self.torrent_row = True

            # count td for find right td
            if self.torrent_row and tag == 'td':
                if self.index_td == 3:
                    self.write = "size"
                elif self.index_td == 4:
                    self.write = "seeds"
                elif self.index_td == 5:
                    self.write = "leech"

                self.index_td += 1

            # search for torrent link by classes r0 or r1
            if self.torrent_row and tag == 'a':
                for name, value in attrs:
                    if name == 'class' and 'r' in value:
                        self.torrent['link'] = 'http://dl.kinozal.tv/download.php?id=' + attrs[0][1].split('=')[1]
                        self.torrent['desc_link'] = self.url + attrs[0][1]
                        self.write = "name"

            # search for right div with class paginator
            if self.found_torrents == 50 and tag == 'div':
                for name, value in attrs:
                    if name == 'class' and value == 'paginator':
                        self.paginator = True

            # search for block with page numbers
            if self.paginator and tag == 'li':
                self.pages += 1

        def handle_endtag(self, tag):
            # detecting that torrent row is closed and print all collected data
            if self.torrent_row and tag == 'tr':
                self.torrent["engine_url"] = self.url
                logging.debug('self.torrent: ' + str(self.torrent))
                prettyPrinter(self.torrent)
                self.torrent = {key: '' for key in self.torrent}
                self.index_td = 0
                self.torrent_row = False
                self.found_torrents += 1

            # detecting that table with result is close
            if self.result_table and tag == 'table':
                self.result_table = False

            # detecting that we found all pagination
            if self.paginator and tag == 'ul':
                self.paginator = False

        def handle_data(self, data: str):
            # detecting that we need write data at this moment
            if self.write and self.result_table:
                if self.write == 'size':
                    data = self.units_convert(data)
                self.torrent[self.write] = data.strip()
                self.write = None

        @staticmethod
        def units_convert(unit):
            # replace size units
            find = unit.split()[1]
            replace = {'ТБ': 'TB', 'ГБ': 'GB', 'МБ': 'MB', 'КБ': 'KB'}[find]

            return unit.replace(find, replace)

        def error(self, message):
            pass

    def download_torrent(self, url: str):
        if self.blocked:
            return
        # choose download method
        if self.config.get("magnet"):
            res = self._catch_error_request(self.url + "/get_srv_details.php?action=2&id=" + url.split("=")[1])
            # magnet = re.search(":\s([A-Z0-9]{40})\<", res.read().decode())[1]
            magnet = 'magnet:?xt=urn:btih:' + res.read().decode()[18:58]
            # return magnet link
            logging.debug(magnet + " " + url)
            print(magnet + " " + url)
        else:
            # Create a torrent file
            file, path = tempfile.mkstemp('.torrent')
            file = os.fdopen(file, "wb")

            # Download url
            response = self._catch_error_request(url)

            # Write it to a file
            file.write(response.read())
            file.close()

            # return file path
            logging.debug(path + " " + url)
            print(path + " " + url)

    def search(self, what, cat='all'):
        if self.blocked:
            return
        query = '{}/browse.php?s={}&c={}'.format(self.url, what.replace(" ", "+"), self.supported_categories[cat])
        response = self._catch_error_request(query)
        parser = self.WorstParser(self.url)
        parser.feed(response.read().decode('cp1251'))
        parser.close()

        # if first request return that we have pages, we do cycle
        if parser.pages:
            for x in range(1, parser.pages):
                response = self._catch_error_request('{}&page={}'.format(query, x))
                parser.feed(response.read().decode('cp1251'))
                parser.close()

        # parser = self.WorstParser(self.url)
        # previous = 1
        # # loop while current not float or privues not equal current
        # while not parser.found_torrents % 50:
        #     query = '{}/browse.php?s={}&c={}&page={}'.format(self.url, what.replace(" ", "+"),
        #                                                      self.supported_categories[cat],
        #                                                      round((parser.found_torrents + 50) // 50) - 1)
        #     response = self._catch_error_request(query)
        #     parser.feed(response.read().decode('cp1251'))
        #     if previous == parser.found_torrents:
        #         break
        #     previous = parser.found_torrents
        # parser.close()

        logging.debug("--- {} seconds ---".format(time.time() - start_time))
        logging.info("Found torrents: {}".format(parser.found_torrents))

    def _catch_error_request(self, url='', data=None):
        url = url if url else self.url

        try:
            response = self.session.open(url, data)
            # Only continue if response status is OK.
            if response.getcode() != 200:
                logging.error('Unable connect')
                raise HTTPError(response.geturl(), response.getcode(),
                                "HTTP request to {} failed with status: {}".format(url, response.getcode()),
                                response.info(), None)
        except (URLError, HTTPError) as e:
            logging.error(e)
            raise e

        # checking that tracker is'nt blocked
        self.blocked = False
        if self.url not in response.geturl():
            logging.warning("{} is blocked. Try proxy or another proxy".format(self.url))
            self.blocked = True

        return response


if __name__ == "__main__":
    kinozal_se = kinozal()
    # kinozal_se.download_torrent("http://kinozal.tv/details.php?id=1263407")
    kinozal_se.search('supernatural')
