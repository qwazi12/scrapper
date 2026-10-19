"""Requested length is editorial intent, independent of measured mux duration."""
from copy import deepcopy
import math


def seconds(minutes):
    if type(minutes) not in (int,float) or not math.isfinite(minutes) or minutes<=0:return None
    return round(minutes*60,3)


def valid(intent,kind):
    if not isinstance(intent,dict) or type(intent.get('schema')) is not int or intent['schema']!=1 or intent.get('kind')!=kind:return False
    target=intent.get('target_seconds')
    if type(target) not in (int,float) or not math.isfinite(target) or target<=0:return False
    if kind=='studio':return target<=600 and intent.get('basis')=='requested approximate complete export length'
    if kind=='countdown':
        count,per_title=intent.get('segment_count'),intent.get('segment_seconds')
        return (type(count) is int and count in (5,10,15) and type(per_title) in (int,float)
            and math.isfinite(per_title) and 24<=per_title<=180 and abs(target-count*per_title)<.001
            and intent.get('basis')=='requested segment total; final bookends add time')
    return False


def requested(project):
    # Immutable sources carry their original intent. Never consult a live draft
    # or assume an old custom Countdown used today's per-format defaults.
    if hasattr(project,'duration_intent'):
        intent=project.duration_intent
        if isinstance(intent,dict) and valid(intent,intent.get('kind')):return deepcopy(intent)
        return {'schema':1,'kind':'unconfirmed','target_seconds':None,'basis':'retained requested length is unconfirmed; review and resend from studio'}
    if hasattr(project,'target_minutes'):
        return {'schema':1,'kind':'studio','target_seconds':seconds(project.target_minutes),
            'basis':'requested approximate complete export length'}
    if hasattr(project,'fmt'):
        from .countdown import COUNTS,SEGMENT_MINUTES
        count=COUNTS.get(project.fmt)
        minutes=(project.review or {}).get('segment_minutes')
        per_title=seconds(SEGMENT_MINUTES.get(project.fmt) if minutes is None else minutes)
        return {'schema':1,'kind':'countdown','segment_count':count,'segment_seconds':per_title,
            'target_seconds':round(count*per_title,3) if count and per_title else None,
            'basis':'requested segment total; final bookends add time'}
    return None


def comparison(project):
    intent=requested(project)
    if intent is None:return None
    actual=((project.render or {}).get('quality') or {}).get('seconds')
    if type(actual) not in (int,float) or not math.isfinite(actual) or actual<=0:actual=None
    return {**intent,'actual_seconds':actual,'review_tolerance_percent':25,
        'scope':'Approximate length review heuristic; this does not measure narrative quality or require padding.'}
