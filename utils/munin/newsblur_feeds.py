#!/usr/bin/env python 
from utils.munin.base import MuninGraph
from apps.rss_feeds.models import Feed, DuplicateFeed
from apps.reader.models import UserSubscription

graph_config = {
    'graph_category' : 'NewsBlur',
    'graph_title' : 'NewsBlur Feeds & Subscriptions',
    'graph_vlabel' : 'Feeds & Subscribers',
    'feeds.label': 'feeds',
    'subscriptions.label': 'subscriptions',
}

metrics = {
    'feeds': Feed.objects.count(),
    'subscriptions': UserSubscription.objects.count(),
}

if __name__ == '__main__':
    MuninGraph(graph_config, metrics).run()
