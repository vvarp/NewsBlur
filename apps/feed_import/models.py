from collections import defaultdict
from django.db import models
from django.contrib.auth.models import User
from apps.rss_feeds.models import Feed, DuplicateFeed
from apps.reader.models import UserSubscription, UserSubscriptionFolders
from apps.rss_feeds.tasks import NewFeeds
from celery.task import Task
import datetime
import lxml.etree
from utils import json_functions as json, urlnorm
import utils.opml as opml
from utils import log as logging
from hashlib import md5
from django.utils import simplejson
import urllib, urllib2

class OAuthToken(models.Model):
    user = models.OneToOneField(User, null=True, blank=True)
    session_id = models.CharField(max_length=50, null=True, blank=True)
    remote_ip = models.CharField(max_length=50, null=True, blank=True)
    request_token = models.CharField(max_length=50)
    request_token_secret = models.CharField(max_length=50)
    access_token = models.CharField(max_length=50)
    access_token_secret = models.CharField(max_length=50)
    created_date = models.DateTimeField(default=datetime.datetime.now)
    

class Importer:

    def clear_feeds(self):
        UserSubscriptionFolders.objects.filter(user=self.user).delete()
        UserSubscription.objects.filter(user=self.user).delete()

    
class OPMLImporter(Importer):
    
    def __init__(self, opml_xml, user):
        self.user = user
        self.opml_xml = opml_xml

    def process(self):
        outline = opml.from_string(self.opml_xml)
        self.clear_feeds()
        folders = self.process_outline(outline)
        UserSubscriptionFolders.objects.create(user=self.user, folders=json.encode(folders))
        return folders
        
    def process_outline(self, outline):
        folders = []
        for item in outline:
            if not hasattr(item, 'xmlUrl'):
                folder = item
                # if hasattr(folder, 'text'):
                #     logging.info(' ---> [%s] ~FRNew Folder: %s' % (self.user, folder.text))
                folders.append({folder.text: self.process_outline(folder)})
            elif hasattr(item, 'xmlUrl'):
                feed = item
                if not hasattr(feed, 'htmlUrl'):
                    setattr(feed, 'htmlUrl', None)
                if not hasattr(feed, 'title') or not feed.title:
                    setattr(feed, 'title', feed.htmlUrl or feed.xmlUrl)
                feed_address = urlnorm.normalize(feed.xmlUrl)
                feed_link = urlnorm.normalize(feed.htmlUrl)
                if len(feed_address) > Feed._meta.get_field('feed_address').max_length:
                    continue
                if feed_link and len(feed_link) > Feed._meta.get_field('feed_link').max_length:
                    continue
                if len(feed.title) > Feed._meta.get_field('feed_title').max_length:
                    feed.title = feed.title[:255]
                # logging.info(' ---> \t~FR%s - %s - %s' % (feed.title, feed_link, feed_address,))
                feed_data = dict(feed_address=feed_address, feed_link=feed_link, feed_title=feed.title)
                # feeds.append(feed_data)

                # See if it exists as a duplicate first
                duplicate_feed = DuplicateFeed.objects.filter(duplicate_address=feed_address)
                if duplicate_feed:
                    feed_db = duplicate_feed[0].feed
                else:
                    feed_data['active_subscribers'] = 1
                    feed_data['num_subscribers'] = 1
                    feed_db, _ = Feed.objects.get_or_create(feed_address=feed_address,
                                                            defaults=dict(**feed_data))
                    
                us, _ = UserSubscription.objects.get_or_create(
                    feed=feed_db, 
                    user=self.user,
                    defaults={
                        'needs_unread_recalc': True,
                        'mark_read_date': datetime.datetime.utcnow() - datetime.timedelta(days=1),
                        'active': self.user.profile.is_premium,
                    }
                )
                if self.user.profile.is_premium and not us.active:
                    us.active = True
                    us.save()
                folders.append(feed_db.pk)
        return folders
        

class GoogleReaderImporter(Importer):
    
    def __init__(self, feeds_xml, user):
        self.user = user
        self.feeds_xml = feeds_xml
        self.subscription_folders = []
        
    def process(self):
        self.clear_feeds()
        self.parse()

        folders = defaultdict(list)
        for item in self.feeds:
            folders = self.process_item(item, folders)
        # print dict(folders)
        self.rearrange_folders(folders)
        logging.info(" ---> [%s] ~BB~FW~SBGoogle Reader import: ~BT~FW%s" % (self.user, self.subscription_folders))
        UserSubscriptionFolders.objects.get_or_create(user=self.user, defaults=dict(
                                                      folders=json.encode(self.subscription_folders)))


    def parse(self):
        self.feeds = lxml.etree.fromstring(self.feeds_xml).xpath('/object/list/object')
    
    def process_item(self, item, folders):
        feed_title = item.xpath('./string[@name="title"]') and \
                        item.xpath('./string[@name="title"]')[0].text
        feed_address = item.xpath('./string[@name="id"]') and \
                        item.xpath('./string[@name="id"]')[0].text.replace('feed/', '')
        feed_link = item.xpath('./string[@name="htmlUrl"]') and \
                        item.xpath('./string[@name="htmlUrl"]')[0].text
        category = item.xpath('./list[@name="categories"]/object/string[@name="label"]') and \
                        item.xpath('./list[@name="categories"]/object/string[@name="label"]')[0].text
        
        if not feed_address:
            feed_address = feed_link
        
        try:
            feed_link = urlnorm.normalize(feed_link)
            feed_address = urlnorm.normalize(feed_address)

            if len(feed_address) > Feed._meta.get_field('feed_address').max_length:
                return folders

            # See if it exists as a duplicate first
            duplicate_feed = DuplicateFeed.objects.filter(duplicate_address=feed_address)
            if duplicate_feed:
                feed_db = duplicate_feed[0].feed
            else:
                feed_data = dict(feed_address=feed_address, feed_link=feed_link, feed_title=feed_title)
                feed_data['active_subscribers'] = 1
                feed_data['num_subscribers'] = 1
                feed_db, _ = Feed.objects.get_or_create(feed_address=feed_address,
                                                        defaults=dict(**feed_data))

            us, _ = UserSubscription.objects.get_or_create(
                feed=feed_db, 
                user=self.user,
                defaults={
                    'needs_unread_recalc': True,
                    'mark_read_date': datetime.datetime.utcnow() - datetime.timedelta(days=1),
                    'active': self.user.profile.is_premium,
                }
            )
            if not category: category = "Root"
            folders[category].append(feed_db.pk)
        except Exception, e:
            logging.info(' *** -> Exception: %s' % e)
            
        return folders
        
    def rearrange_folders(self, folders, depth=0):
        for folder, items in folders.items():
            if folder == 'Root':
                self.subscription_folders += items
            else:
                # folder_parents = folder.split(u' \u2014 ')
                self.subscription_folders.append({folder: items})


class FeverImporter(Importer):
    
    def __init__(self, user, fever_url, fever_username, fever_password):
        self.user = user
        self.fever_url = fever_url
        self.api_key_encoded = urllib.urlencode({"api_key": 
                                    md5("%s:%s" % (fever_username, fever_password)).hexdigest()})
    
    def process(self):
        
        self.clear_feeds()
        folders = defaultdict(list)
        
        # Read feeds and groups first
        request = urllib2.urlopen(self.fever_url + "?api&groups&feeds", self.api_key_encoded)
        data = simplejson.loads(request.read())
        
        # Make sure API version is right
        
        if data["api_version"] == 2:
        
            # Process groups/feeds
            for feed in data["feeds"]:

                feed_link = urlnorm.normalize(feed["site_url"])
                feed_address = urlnorm.normalize(feed["url"])
            
                # See if it exists as a duplicate first
                duplicate_feed = DuplicateFeed.objects.filter(duplicate_address=feed_address)
                if duplicate_feed:
                    feed_db = duplicate_feed[0].feed
                else:
                    feed_data = dict(feed_address=feed_address, feed_link=feed_link, feed_title=feed["title"])
                    feed_data['active_subscribers'] = 1
                    feed_data['num_subscribers'] = 1
                    feed_db, _ = Feed.objects.get_or_create(feed_address=feed_address,
                                                            defaults=dict(**feed_data))

                us, _ = UserSubscription.objects.get_or_create(
                    feed=feed_db, 
                    user=self.user,
                    defaults={
                        'needs_unread_recalc': True,
                        'active': self.user.profile.is_premium,
                    }
                )
            
                if feed["is_spark"] == 1:
                    folders["_Sparks"].append(feed_db.pk)
                else:
                    group_id = [k["group_id"] for k in data["feeds_groups"] if str(feed["id"]) in k["feed_ids"].split(",")]
                    if len(group_id):                    
                        category = [k["title"] for k in data["groups"] if int(group_id[0]) == k["id"]][0]
                    else:
                        category = "_Orphaned"
                    folders[category].append(feed_db.pk)
                
            # TODO: Import starred items
            
            # Wrap up
            UserSubscriptionFolders.objects.get_or_create(user=self.user, defaults=dict(folders=json.encode([folders])))
        
    

def queue_new_feeds(user):
    new_feeds = UserSubscription.objects.filter(user=user, 
                                                feed__fetched_once=False, 
                                                active=True).values('feed_id')
    new_feeds = list(set([f['feed_id'] for f in new_feeds]))
    logging.info(" ---> [%s] ~BB~FW~SBQueueing NewFeeds: ~FC(%s) %s" % (user, len(new_feeds), new_feeds))
    size = 4
    publisher = Task.get_publisher(exchange="new_feeds")
    for t in (new_feeds[pos:pos + size] for pos in xrange(0, len(new_feeds), size)):
        NewFeeds.apply_async(args=(t,), queue="new_feeds", publisher=publisher)
    publisher.connection.close()   