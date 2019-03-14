#!/usr/bin/env python
"""
Description: Sync the watch status from one user to another across multiple servers including from Tautulli
Author: Blacktwin
Requires: requests, plexapi, argparse

Enabling Scripts in Tautulli:
Taultulli > Settings > Notification Agents > Add a Notification Agent > Script

Configuration:
Taultulli > Settings > Notification Agents > New Script > Configuration:

 Script Name: sync_watch_status.py
 Set Script Timeout: default
 Description: Sync watch status
 Save

Triggers:
Taultulli > Settings > Notification Agents > New Script > Triggers:

 Check: Notify on Watched
 Save

Conditions:
Taultulli > Settings > Notification Agents > New Script > Conditions:

 Set Conditions: [{username} | {is} | {user_to_sync_from} ]
 Save

Script Arguments:
Taultulli > Settings > Notification Agents > New Script > Script Arguments:

 Select: Notify on Watched
 Arguments: --ratingKey {rating_key} --userFrom Tautulli=Tautulli --userTo "Username2=Server1" "Username3=Server1"

 Save
 Close

 Example:
    Set in Tautulli in script notification agent (above) or run manually (below)

    sync_watch_status.py --userFrom USER1=Server1 --userTo USER2=Server1 --libraries Movies
       - Synced watch status from Server1 {title from library} to {USER2}'s account on Server1.

    sync_watch_status.py --userFrom USER1=Server2 --userTo USER2=Server1 USER3=Server1 --libraries Movies "TV Shows"
       - Synced watch status from Server2 {title from library} to {USER2 or USER3}'s account on Server1.

    sync_watch_status.py --userFrom USER1=Tautulli --userTo USER2=Server1 USER3=Server2 --libraries Movies "TV Shows"
       - Synced watch statuses from Tautulli {title from library} to {USER2 or USER3}'s account on selected servers.

"""
import argparse
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from plexapi.server import CONFIG
from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException

import pprint

pp = pprint.PrettyPrinter(indent=4)

# Using CONFIG file
PLEX_TOKEN = ''
TAUTULLI_URL = ''
TAUTULLI_APIKEY = ''

if not PLEX_TOKEN:
    PLEX_TOKEN = CONFIG.data['auth'].get('server_token')
if not TAUTULLI_URL:
    TAUTULLI_URL = CONFIG.data['auth'].get('tautulli_baseurl')
if not TAUTULLI_APIKEY:
    TAUTULLI_APIKEY = CONFIG.data['auth'].get('tautulli_apikey')

VERIFY_SSL = False


class Connection:
    def __init__(self, url=None, apikey=None, verify_ssl=False):
        self.url = url
        self.apikey = apikey
        self.session = Session()
        self.adapters = HTTPAdapter(max_retries=3,
                                    pool_connections=1,
                                    pool_maxsize=1,
                                    pool_block=True)
        self.session.mount('http://', self.adapters)
        self.session.mount('https://', self.adapters)
        
        # Ignore verifying the SSL certificate
        if verify_ssl is False:
            self.session.verify = False
            # Disable the warning that the request is insecure, we know that...
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Library(object):
    def __init__(self, data=None):
        d = data or {}
        self.title = d['section_name']
        self.key = d['section_id']


class Metadata(object):
    def __init__(self, data=None):
        d = data or {}
        self.type = d['media_type']
        self.grandparentTitle = d['grandparent_title']
        self.parentIndex = d['parent_media_index']
        self.index = d['media_index']
        if self.type == 'episode':
            ep_name = d['full_title'].partition('-')[-1]
            self.title = ep_name.lstrip()
        else:
            self.title = d['full_title']
        
        # For History
        try:
            if d['watched_status']:
                self.watched_status = d['watched_status']
        except KeyError:
            pass
        # For Metadata
        try:
            if d["library_name"]:
                self.libraryName = d['library_name']
        except KeyError:
            pass


class Tautulli:
    def __init__(self, connection):
        self.connection = connection
    
    def _call_api(self, cmd, payload, method='GET'):
        payload['cmd'] = cmd
        payload['apikey'] = self.connection.apikey
        
        try:
            response = self.connection.session.request(method, self.connection.url + '/api/v2', params=payload)
        except RequestException as e:
            print("Tautulli request failed for cmd '{}'. Invalid Tautulli URL? Error: {}".format(cmd, e))
            return
        
        try:
            response_json = response.json()
        except ValueError:
            print("Failed to parse json response for Tautulli API cmd '{}'".format(cmd))
            return
        
        if response_json['response']['result'] == 'success':
            return response_json['response']['data']
        else:
            error_msg = response_json['response']['message']
            print("Tautulli API cmd '{}' failed: {}".format(cmd, error_msg))
            return
    
    def get_watched_history(self, user, section_id, start, length):
        """Call Tautulli's get_history api endpoint"""
        payload = {"user": user,
                   "section_id": section_id,
                   'start': start,
                   'length': length,
                   'order_column': 'full_title',
                   'order_dir': 'asc'}
        
        history = self._call_api('get_history', payload)
        
        return [d for d in history['data'] if d['watched_status'] == 1]
    
    def get_metadata(self, rating_key):
        """Call Tautulli's get_metadata api endpoint"""
        
        payload = {"rating_key": rating_key}
        return self._call_api('get_metadata', payload)
    
    def get_libraries(self):
        """Call Tautulli's get_libraries api endpoint"""
        
        payload = {}
        return self._call_api('get_libraries', payload)


class Plex:
    def __init__(self, token, url=None):
        if token and not url:
            self.account = MyPlexAccount(token)
        if token and url:
            session = Connection().session
            self.server = PlexServer(baseurl=url, token=token, session=session)
    
    def admin_servers(self):
        """
        Returns
        -------
        data: dict
        """
        resources = {}
        for resource in self.account.resources():
            if 'server' in [resource.provides] and resource.owned == True:
                resources[resource.name] = resource
        
        return resources
    
    def all_users(self):
        """
        Returns
        -------
        data: dict
        """
        users = {self.account.title: self.account}
        for user in self.account.users():
            users[user.title] = user
        
        return users
    
    def all_sections(self):
        """
        Returns
        -------
        data: dict
        """
        data = {}
        servers = self.admin_servers()
        print('Connecting to admin server(s) for section info...')
        for name, server in servers.items():
            connect = server.connect()
            sections = {section.title: section for section in connect.library.sections()}
            data[name] = sections
        
        return data
    
    def users_access(self):
        """
        Returns
        -------
        data: dict
        """
        all_users = self.all_users().values()
        admin_servers = self.admin_servers()
        all_sections = self.all_sections()
        
        data = {self.account.title: {"account": self.account}}
        
        for user in all_users:
            if not data.get(user.title):
                servers = []
                for server in user.servers:
                    if admin_servers.get(server.name):
                        access = {}
                        sections = {section.title: section for section in server.sections()
                                    if section.shared == True}
                        access['server'] = {server.name: admin_servers.get(server.name)}
                        access['sections'] = sections
                        servers += [access]
                        data[user.title] = {'account': user,
                                            'access': servers}
            else:
                # Admin account
                servers = []
                for name, server in admin_servers.items():
                    access = {}
                    sections = all_sections.get(name)
                    access['server'] = {name: server}
                    access['sections'] = sections
                    servers += [access]
                    data[user.title] = {'account': user,
                                        'access': servers}
        return data


def connect_to_server(server_obj, user_account):
    """
    Parameters
    ----------
    server_obj: class
    user_account: class

    Returns
    -------
    user_connection.server: class
    """
    server_name = server_obj.name
    user = user_account.title
    
    print('Connecting {} to {}...'.format(user, server_name))
    server_connection = server_obj.connect()
    baseurl = server_connection._baseurl.split('.')
    url = ''.join([baseurl[0].replace('-', '.'),
                   baseurl[-1].replace('direct', '')])
    if user_account.title == Plex(PLEX_TOKEN).account.title:
        token = PLEX_TOKEN
    else:
        token = user_account.get_token(server_connection.machineIdentifier)
    
    user_connection = Plex(url=url, token=token)
    
    return user_connection.server


def check_users_access(user, server_name, libraries=None):
    """
    Parameters
    ----------
    user: str
    server_name: str
    libraries: list

    Returns
    -------
    server_connection: class
    """
    try:
        _user = plex_admin.users_access().get(user)
        for access in _user['access']:
            server = access.get("server")
            # Check user access to server
            if server.get(server_name):
                server_obj = server.get(server_name)
                # If syncing by libraries, check library access
                if libraries:
                    library_check = any(lib.title in access.get("sections").keys() for lib in libraries)
                    # Check user access to library
                    if library_check:
                        server_connection = connect_to_server(server_obj, _user['account'])
                        return server_connection
                    
                    elif not library_check:
                        print("User does not have access to this library.")
                # Not syncing by libraries
                else:
                    server_connection = connect_to_server(server_obj, _user['account'])
                    return server_connection
            # else:
            #     print("User does not have access to this server: {}.".format(server_name))
    except KeyError:
        print('User name is incorrect.')
        print(", ".join(plex_admin.all_users().keys()))
        exit()


def sync_watch_status(watched, section, accountTo, userTo):
    """
    Parameters
    ----------
    watched: list
    section: class
        Section class of sync from server
    userTo: str
        User's server class of sync to user

    """
    # Check sections for watched items
    print('Marking watched...')
    sectionTo = accountTo.library.section(section)
    for item in watched:
        try:
            # .get retrieves a partial object
            if item.type == 'episode':
                show_name = item.grandparentTitle
                ep_name = item.title
                title = "{} {}".format(show_name, ep_name)
                show = sectionTo.get(show_name)
                watch_check = show.episode(season=item.parentIndex, episode=item.index)
            else:
                title = item.title
                watch_check = sectionTo.get(title)
            # .fetchItem retrieves a full object
            fetch_check = sectionTo.fetchItem(watch_check.key)
            # If item is already watched ignore
            if not fetch_check.isWatched:
                # todo-me should watched count be synced?
                fetch_check.markWatched()
                print("Synced watched status of {} to account {}...".format(title, userTo))
        
        except Exception as e:
            print(e)
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Sync watch status from one user to others.",
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--libraries', nargs='*', metavar='library',
                        help='Libraries to scan for watched content.')
    parser.add_argument('--ratingKey', nargs="?", type=str,
                        help='Rating key of item whose watch status is to be synced.')
    requiredNamed = parser.add_argument_group('required named arguments')
    requiredNamed.add_argument('--userFrom', metavar='user=server', required=True,
                               type=lambda kv: kv.split("="), default=["", ""],
                               help='Select user and server to sync from')
    requiredNamed.add_argument('--userTo', nargs='*', metavar='user=server', required=True,
                               type=lambda kv: kv.split("="),
                               help='Select user and server to sync to.')
    
    opts = parser.parse_args()
    # print(opts)
    tautulli_server = ''
    
    libraries = []
    all_sections = {}
    watchedFrom = ''
    count = 25
    start = 0
    plex_admin = Plex(PLEX_TOKEN)
    
    userFrom, serverFrom = opts.userFrom
    
    if serverFrom == "Tautulli":
        # Create a Tautulli instance
        tautulli_server = Tautulli(Connection(url=TAUTULLI_URL.rstrip('/'),
                                              apikey=TAUTULLI_APIKEY,
                                              verify_ssl=VERIFY_SSL))
    
    if serverFrom == "Tautulli" and opts.libraries:
        _sections = {}
        # Pull all libraries from Tautulli
        tautulli_sections = tautulli_server.get_libraries()
        for section in tautulli_sections:
            section_obj = Library(section)
            _sections[section_obj.title] = section_obj
        all_sections[serverFrom] = _sections
    elif serverFrom != "Tautulli" and opts.libraries:
        all_sections = plex_admin.all_sections()
    
    # Defining libraries
    if opts.libraries:
        for library in opts.libraries:
            if all_sections.get(serverFrom):
                if all_sections.get(serverFrom).get(library):
                    libraries.append(all_sections.get(serverFrom).get(library))
                else:
                    print("No matching library name '{}'".format(library))
                    exit()
            
            else:
                print("No matching server name '{}'".format(serverFrom))
                exit()
    
    # If userFrom is Plex Admin
    # if userFrom == plex_admin.account.title and serverFrom != "Tautulli" and opts.libraries:
    #     resource = plex_admin.admin_servers().get(serverFrom)
    #     print('Connecting {} to {}...'.format(userFrom, serverFrom))
    #     server_connection = resource.connect()
    #     baseurl = server_connection._baseurl.split('.')
    #     url = ''.join([baseurl[0].replace('-', '.'),
    #                    baseurl[-1].replace('direct', '')])
    #
    #     token = PLEX_TOKEN
    #     admin_connection = Plex(url=url, token=token)
    #     watchedFrom = admin_connection.server
    if serverFrom != "Tautulli" and opts.libraries:
        watchedFrom = check_users_access(userFrom, serverFrom, libraries)
    
    if libraries:
        print("Finding watched items in libraries...")
        plexTo = []
        
        for user, server_name in opts.userTo:
            plexTo.append([user, check_users_access(user, server_name, libraries)])
        
        for _library in libraries:
            watched_lst = []
            print("Checking {}'s library: '{}' watch statuses...".format(userFrom, _library.title))
            if tautulli_server:
                while True:
                    # Getting all watched history for userFrom
                    tt_watched = tautulli_server.get_watched_history(userFrom, _library.key, start, count)
                    if all([tt_watched]):
                        start += count
                        for item in tt_watched:
                            watched_lst.append(Metadata(item))
                        continue
                    elif not all([tt_watched]):
                        break
                    start += count
            else:
                # Check library for watched items
                sectionFrom = watchedFrom.library.section(_library.title)
                if _library.type == 'show':
                    for episode in sectionFrom.searchEpisodes(unwatched=False):
                        watched_lst.append(episode)
                else:
                    for item in sectionFrom.search(unwatched=False):
                        watched_lst.append(item)
            
            for user in plexTo:
                username, server = user
                sync_watch_status(watched_lst, _library.title, server, username)
    
    elif opts.ratingKey and userFrom == "Tautulli" and serverFrom == "Tautulli":
        print('Request from Tautulli notification agent to update watch status')
        plexTo = []
        
        for user, server_name in opts.userTo:
            # Check access and connect
            plexTo.append([user, check_users_access(user, server_name, libraries)])
        
        for user in plexTo:
            username, server = user
            item = Metadata(tautulli_server.get_metadata(opts.ratingKey))
            sync_watch_status([item], item.libraryName, server, username)
    
    else:
        print("You aren't using this script correctly... bye!")