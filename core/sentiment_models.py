"""Optional on-demand low-CPU VADER baseline; no network connections.

Requires `pip install vaderSentiment`. Output is point-in-time numeric input for
trend_sentiment, NOT a verified prediction or new trading signal.
"""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
from typing import Literal
from pydantic import BaseModel,Field,field_validator

class TextDocument(BaseModel):
    source:Literal['telegram','rss','x','website','other']
    post_id:str=Field(min_length=1,max_length=180)
    first_observed_at:datetime
    symbol:str=Field(min_length=1,max_length=32)
    text:str=Field(min_length=1,max_length=4000)
    language:Literal['en']='en'
    @field_validator('first_observed_at')
    @classmethod
    def with_timezone(cls,v):
        if v.utcoffset() is None:raise ValueError('Observed time requires offset')
        return v.astimezone(timezone.utc)

def vader_batch(documents:list[TextDocument],scorer=None):
    if len(documents)>200:raise ValueError('Batch too large')
    if scorer is None:
        try:from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        except ImportError as e:raise RuntimeError('Install optional: pip install vaderSentiment') from e
        scorer=SentimentIntensityAnalyzer()
    return [{'source':x.source,'post_id':x.post_id,'first_observed_at':x.first_observed_at.isoformat(),
             'symbol':x.symbol,'score':float(scorer.polarity_scores(x.text)['compound']),
             'model':'vaderSentiment','model_version':'local-optional-lexicon',
             'text_sha256':sha256(x.text.encode('utf-8')).hexdigest(),
             'caveat':'English social lexicon baseline only. Not calibrated for financial prices.'}
            for x in documents]
