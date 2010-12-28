import datetime
from utils import log as logging
from django.shortcuts import get_object_or_404
from django.http import HttpResponseForbidden
from django.db.models import Q
# from django.db import IntegrityError
from apps.rss_feeds.models import Feed, merge_feeds
from apps.reader.models import UserSubscription
from utils.user_functions import ajax_login_required
from utils import json_functions as json, feedfinder
from utils.feed_functions import relative_timeuntil, relative_timesince

@json.json_view
def feed_autocomplete(request):
    query = request.GET['term']
    feeds = []
    for field in ['feed_address', 'feed_link', 'feed_title']:
        if not feeds:
            feeds = Feed.objects.filter(**{
                '%s__icontains' % field: query,
                'num_subscribers__gt': 1,
            }).exclude(
                Q(**{'%s__icontains' % field: 'token'}) |
                Q(**{'%s__icontains' % field: 'private'})
            ).only(
                'feed_title', 
                'feed_address', 
                'num_subscribers'
            ).order_by('-num_subscribers')[:5]
    
    logging.info(" ---> [%s] ~FRAdd Search: ~SB%s ~FG(%s matches)" % (request.user, query, len(feeds),))
    
    feeds = [{
        'value': feed.feed_address,
        'label': feed.feed_title,
        'num_subscribers': feed.num_subscribers,
    } for feed in feeds]
    
    return feeds
    
@json.json_view
def load_feed_statistics(request):
    stats = dict()
    feed_id = request.GET['feed_id']
    feed = get_object_or_404(Feed, pk=feed_id)
    feed.save_feed_story_history_statistics()
    
    # Dates of last and next update
    stats['last_update'] = relative_timesince(feed.last_update)
    stats['next_update'] = relative_timeuntil(feed.next_scheduled_update)
    
    # Minutes between updates
    update_interval_minutes, random_factor = feed.get_next_scheduled_update()
    stats['update_interval_minutes'] = update_interval_minutes
    
    # Stories per month - average and month-by-month breakout
    average_stories_per_month, story_count_history = feed.average_stories_per_month, feed.story_count_history
    stats['average_stories_per_month'] = average_stories_per_month
    stats['story_count_history'] = story_count_history and json.decode(story_count_history)
    
    # Subscribers
    stats['subscriber_count'] = feed.num_subscribers
    stats['stories_last_month'] = feed.stories_last_month
    stats['last_load_time'] = feed.last_load_time
    stats['premium_subscribers'] = feed.premium_subscribers
    stats['active_subscribers'] = feed.active_subscribers
    
    logging.info(" ---> [%s] ~FBStatistics: ~SB%s ~FG(%s/%s/%s subs)" % (request.user, feed, feed.num_subscribers, feed.active_subscribers, feed.premium_subscribers,))
    
    return stats
    
@ajax_login_required
@json.json_view
def exception_retry(request):
    feed_id = request.POST['feed_id']
    reset_fetch = json.decode(request.POST['reset_fetch'])
    feed = get_object_or_404(Feed, pk=feed_id)
    
    feed.next_scheduled_update = datetime.datetime.utcnow()
    feed.has_page_exception = False
    feed.has_feed_exception = False
    feed.active = True
    if reset_fetch:
        logging.info(' ---> [%s] ~FRRefreshing exception feed: ~SB%s' % (request.user, feed))
        feed.fetched_once = False
    else:
        logging.info(' ---> [%s] ~FRForcing refreshing feed: ~SB%s' % (request.user, feed))
        feed.fetched_once = True
    feed.save()
    
    feed.update(force=True, compute_scores=False)
    usersub = UserSubscription.objects.get(user=request.user, feed=feed)
    usersub.calculate_feed_scores(silent=False)
    
    return {'code': 1}
    
    
@ajax_login_required
@json.json_view
def exception_change_feed_address(request):
    feed_id = request.POST['feed_id']
    feed = get_object_or_404(Feed, pk=feed_id)
    feed_address = request.POST['feed_address']
    
    if not feed.has_feed_exception and not feed.has_page_exception:
        logging.info(" ***> [%s] ~BRIncorrect feed address change: ~SB%s" % (request.user, feed))
        return HttpResponseForbidden()
        
    feed.has_feed_exception = False
    feed.active = True
    feed.fetched_once = False
    feed.feed_address = feed_address
    feed.next_scheduled_update = datetime.datetime.utcnow()
    retry_feed = feed
    duplicate_feed_id = feed.save()
    if duplicate_feed_id:
        original_feed = Feed.objects.get(pk=duplicate_feed_id)
        retry_feed = original_feed
        original_feed.next_scheduled_update = datetime.datetime.utcnow()
        original_feed.has_feed_exception = False
        original_feed.active = True
        original_feed.save()
        merge_feeds(original_feed.pk, feed.pk)
    
    logging.info(" ---> [%s] ~FRFixing feed exception by address: ~SB%s" % (request.user, retry_feed.feed_address))
    retry_feed.update()
    
    return {'code': 1}
    
@ajax_login_required
@json.json_view
def exception_change_feed_link(request):
    feed_id = request.POST['feed_id']
    feed = get_object_or_404(Feed, pk=feed_id)
    feed_link = request.POST['feed_link']
    code = -1
    
    if not feed.has_page_exception and not feed.has_feed_exception:
        logging.info(" ***> [%s] ~BRIncorrect feed link change: ~SB%s" % (request.user, feed))
        # This Forbidden-403 throws an error, which sounds pretty good to me right now
        return HttpResponseForbidden()
    
    retry_feed = feed
    feed_address = feedfinder.feed(feed_link)
    if feed_address:
        code = 1
        feed.has_page_exception = False
        feed.active = True
        feed.fetched_once = False
        feed.feed_link = feed_link
        feed.feed_address = feed_address
        feed.next_scheduled_update = datetime.datetime.utcnow()
        duplicate_feed_id = feed.save()
        if duplicate_feed_id:
            original_feed = Feed.objects.get(pk=duplicate_feed_id)
            retry_feed = original_feed
            original_feed.next_scheduled_update = datetime.datetime.utcnow()
            original_feed.has_page_exception = False
            original_feed.active = True
            original_feed.save()
    
    logging.info(" ---> [%s] ~FRFixing feed exception by link: ~SB%s" % (request.user, retry_feed.feed_link))
    retry_feed.update()
    
    return {'code': code}
    
    