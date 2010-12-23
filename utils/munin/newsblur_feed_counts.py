#!/usr/bin/env python 
from utils.munin.base import MuninGraph
from apps.rss_feeds.models import Feed, DuplicateFeed
from apps.reader.models import UserSubscription

graph_config = {
    'graph_category' : 'NewsBlur',
    'graph_title' : 'NewsBlur Feed Counts',
    'graph_vlabel' : 'Feeds Feed Counts',
    'exception_feeds.label': 'exception_feeds',
    'exception_pages.label': 'exception_pages',
    'inactive_feeds.label': 'inactive_feeds',
    'duplicate_feeds.label': 'duplicate_feeds',
    'active_feeds.label': 'active_feeds',
}

metrics = {
    'exception_feeds': Feed.objects.filter(has_feed_exception=True).count(),
    'exception_pages': Feed.objects.filter(has_page_exception=True).count(),
    'inactive_feeds': Feed.objects.filter(active=False).count(),
    'duplicate_feeds': DuplicateFeed.objects.count(),
    'active_feeds': Feed.objects.filter(active_subscribers__gt=0).count(),
}

if __name__ == '__main__':
    MuninGraph(graph_config, metrics).run()
