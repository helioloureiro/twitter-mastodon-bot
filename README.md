# twitter-mastodon-bot
A bot that bridges twitter accounts into mastodon

## What was done

* Authenticate on Twitter
* Fetch data from Twitter - long text
* Authenticate on Mastodon
* Post on Mastodon
* Support for hashtags
* Asynchronous data fetch from twitter send to asynchronous data post on mastodon

## What is missing

* Verify limits
* Remove hardcoded sleep and and something based on limits
* Use properly logging (and remove several prints)
* Do proper follow up with RT and other options
* Post images or videos from original post
* Binding from twitter accounts to read to different accounts in mastodon.
    - Perhaps change from current config to yaml as suggested by Guto.

