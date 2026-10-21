# frozen_string_literal: true

# Pagy initializer file (9.4.0)
# Customize only what you really need and notice that the core Pagy works also without any of the following lines.
# Should you think that something is wrong with this file, please search the docs before posting an issue

# Pagy Variables
# See https://ddnexus.github.io/pagy/api/pagy#variables
# All the Pagy::DEFAULT are set for all the Pagy instances but can be overridden per instance by just passing them to
# Pagy.new|Pagy::Countless.new|Pagy::Calendar::*.new or any of the #pagy* controller methods

# Instance variables
# See https://ddnexus.github.io/pagy/api/pagy#instance-variables
Pagy::DEFAULT[:page]   = 1                                  # default
Pagy::DEFAULT[:items]  = 20                                 # default
Pagy::DEFAULT[:outset] = 0                                  # default

# Other Variables
# See https://ddnexus.github.io/pagy/api/pagy#other-variables
Pagy::DEFAULT[:size]       = 7                              # default
Pagy::DEFAULT[:page_param] = :page                          # default
# The :params can be also set as a lambda e.g ->(params){ params.exclude('useless').merge!('custom' => 'useful') }
Pagy::DEFAULT[:params]     = {}                             # default
Pagy::DEFAULT[:fragment]   = ''                             # example '#fragment'
Pagy::DEFAULT[:link_extra] = ''                             # example 'data-remote="true"'
# Pagy::DEFAULT[:i18n_key]   = 'pagy.item_name'             # default

# Rails
# Enable the Pagy::DEFAULT[:url_for_with_params] to use the legacy rails helper method `url_for`.
# Warning: It has a serious performance regression. Using the pagy `:pagy_url_for` instead of `:url_for` is 40x faster!
# See https://ddnexus.github.io/pagy/docs/how-to#customize-the-url
# Pagy::DEFAULT[:url_for_with_params] = true

# Extras
# See https://ddnexus.github.io/pagy/categories/extras

# Backend Extras

# Arel extra: Knows how to add the ORDER SQL bit based on the arel defining the collection
# See https://ddnexus.github.io/pagy/docs/extras/arel
# require 'pagy/extras/arel'

# Array extra: Paginate arrays efficiently, avoiding expensive array-wrapping and without overriding
# See https://ddnexus.github.io/pagy/docs/extras/array
# require 'pagy/extras/array'

# Calendar extra: Add pagination filtering by calendar time unit (year, month, week, day)
# See https://ddnexus.github.io/pagy/docs/extras/calendar
# require 'pagy/extras/calendar'
# Default for each unit
# Pagy::Calendar::Year::DEFAULT[:order]     = :asc        # Time direction of pagination
# Pagy::Calendar::Year::DEFAULT[:format]    = '%Y'        # strftime format
#
# Pagy::Calendar::Quarter::DEFAULT[:order]  = :asc        # Time direction of pagination
# Pagy::Calendar::Quarter::DEFAULT[:format] = '%Y-Q%q'    # strftime format
#
# Pagy::Calendar::Month::DEFAULT[:order]    = :asc        # Time direction of pagination
# Pagy::Calendar::Month::DEFAULT[:format]   = '%Y-%m'     # strftime format
#
# Pagy::Calendar::Week::DEFAULT[:order]     = :asc        # Time direction of pagination
# Pagy::Calendar::Week::DEFAULT[:format]    = '%Y-%W'     # strftime format
#
# Pagy::Calendar::Day::DEFAULT[:order]      = :asc        # Time direction of pagination
# Pagy::Calendar::Day::DEFAULT[:format]     = '%Y-%m-%d'  # strftime format
#
# Uncomment the following lines, if you need calendar localization without using the I18n extra
# module LocalizePagyCalendar
#   def localize(time, opts)
#     ::I18n.l(time, **opts)
#   end
# end
# Pagy::Calendar.prepend LocalizePagyCalendar

# Countless extra: Paginate without any count, saving one query per rendering
# See https://ddnexus.github.io/pagy/docs/extras/countless
# require 'pagy/extras/countless'
# Pagy::DEFAULT[:countless_minimal] = false   # default (eager loading)

# Elasticsearch Rails extra: Paginate `ElasticsearchRails::Results` objects
# See https://ddnexus.github.io/pagy/docs/extras/elasticsearch_rails
# default :pagy_search method: change only if you use also
# the searchkick or meilisearch extra that defines the same
# Pagy::DEFAULT[:elasticsearch_rails_pagy_search] = :pagy_search
# Pagy::DEFAULT[:elasticsearch_rails_search_method] = :search
# require 'pagy/extras/elasticsearch_rails'

# Headers extra: http response headers (and other helpers) useful for API pagination
# See http://ddnexus.github.io/pagy/extras/headers
# require 'pagy/extras/headers'
# Pagy::DEFAULT[:headers] = { page: 'Current-Page',
#                            items: 'Page-Items',
#                            count: 'Total-Count',
#                            pages: 'Total-Pages' }     # default

# Meilisearch extra: Paginate `Meilisearch` results
# See https://ddnexus.github.io/pagy/docs/extras/meilisearch
# default :pagy_search method: change only if you use also
# the elasticsearch_rails or searchkick extra that define the same method
# Pagy::DEFAULT[:meilisearch_pagy_search] = :pagy_search
# Pagy::DEFAULT[:meilisearch_search_method] = :ms_search
# require 'pagy/extras/meilisearch'

# Metadata extra: Provides the pagination metadata to Javascript frameworks like Vue.js, react.js, etc.
# See https://ddnexus.github.io/pagy/docs/extras/metadata
# you must require the frontend helpers internal extra (BEFORE the metadata extra) ONLY if you need also the :sequels
# require 'pagy/extras/frontend_helpers'
# require 'pagy/extras/metadata'
# For performance reasons, you should explicitly set ONLY the metadata you use in the frontend
# Pagy::DEFAULT[:metadata] = %i[scaffold_url page prev next last]   # example

# Searchkick extra: Paginate `Searchkick::Results` objects
# See https://ddnexus.github.io/pagy/docs/extras/searchkick
# default :pagy_search method: change only if you use also
# the elasticsearch_rails or meilisearch extra that defines the same
# DEFAULT[:searchkick_pagy_search] = :pagy_search
# Pagy::DEFAULT[:searchkick_search_method] = :search
# require 'pagy/extras/searchkick'
# uncomment if you are going to use Searchkick.pagy_search
# Searchkick.extend Pagy::Searchkick

# Frontend Extras

# Bootstrap extra: Add nav, nav_js and combo_nav_js helpers and templates for Bootstrap pagination
# See https://ddnexus.github.io/pagy/docs/extras/bootstrap
# require 'pagy/extras/bootstrap'

# Bulma extra: Add nav, nav_js and combo_nav_js helpers and templates for Bulma pagination
# See https://ddnexus.github.io/pagy/docs/extras/bulma
# require 'pagy/extras/bulma'

# Foundation extra: Add nav, nav_js and combo_nav_js helpers and templates for Foundation pagination
# See https://ddnexus.github.io/pagy/docs/extras/foundation
# require 'pagy/extras/foundation'

# Materialize extra: Add nav, nav_js and combo_nav_js helpers for Materialize pagination
# See https://ddnexus.github.io/pagy/docs/extras/materialize
# require 'pagy/extras/materialize'

# Navs extra: Add nav_js and combo_nav_js javascript helpers
# Notice: the other frontend extras add their own framework-styled versions,
# so require this extra only if you need the unstyled version
# See https://ddnexus.github.io/pagy/docs/extras/navs
# require 'pagy/extras/navs'

# Semantic extra: Add nav, nav_js and combo_nav_js helpers for Semantic UI pagination
# See https://ddnexus.github.io/pagy/docs/extras/semantic
# require 'pagy/extras/semantic'

# UIkit extra: Add nav helper and templates for UIkit pagination
# See https://ddnexus.github.io/pagy/docs/extras/uikit
# require 'pagy/extras/uikit'

# Multi size var used by the *_nav_js helpers
# See https://ddnexus.github.io/pagy/docs/extras/navs#steps
# Pagy::DEFAULT[:steps] = { 0 => 5, 540 => 7, 720 => 9 }   # example

# Feature Extras

# Gearbox extra: Automatically change the number of items per page depending on the page number
# See https://ddnexus.github.io/pagy/docs/extras/gearbox
# require 'pagy/extras/gearbox'
# set to false only if you want to make :gearbox_extra an opt-in variable
# Pagy::DEFAULT[:gearbox_extra] = false               # default true
# Pagy::DEFAULT[:gearbox_items] = [15, 30, 60, 100]   # default

# I18n extra: uses the standard i18n gem which is ~18x slower using ~10x more memory
# than the default pagy internal i18n (see above)
# See https://ddnexus.github.io/pagy/docs/extras/i18n
# require 'pagy/extras/i18n'

# Overflow extra: Allow for easy handling of overflowing pages
# See https://ddnexus.github.io/pagy/docs/extras/overflow
require 'pagy/extras/overflow'
Pagy::DEFAULT[:overflow] = :last_page    # default  (other options: :last_page and :exception)

# Support extra: Extra support for features like: incremental, infinite, auto-scroll pagination
# See https://ddnexus.github.io/pagy/docs/extras/support
# require 'pagy/extras/support'

# Trim extra: Remove the page=1 param from links
# See https://ddnexus.github.io/pagy/docs/extras/trim
# require 'pagy/extras/trim'
# set to false only if you want to make :trim_extra an opt-in variable
# Pagy::DEFAULT[:trim_extra] = false # default true

# Standalone extra: Use pagy without any framework or gem
# See https://ddnexus.github.io/pagy/docs/extras/standalone
# require 'pagy/extras/standalone'
# Pagy::DEFAULT[:url] = 'http://www.example.com/subdir'  # optional default

# Rails
# Enable the optional support for action_dispatch/rack media type negotiation, so you can serve your next links with
# the same format of the request instead of the default html. (e.g. JSON next links for JSON requests).
# See https://ddnexus.github.io/pagy/docs/how-to#add-format-to-next-url
# require 'pagy/extras/automated_media_types'

# Rails: extras assets path required by the helpers that use javascript
# (pagy*_nav_js, pagy*_combo_nav_js, and pagy_items_selector_js)
# See https://ddnexus.github.io/pagy/docs/api/javascript#setting-up-the-rails-assets
# Rails.application.config.assets.paths << Pagy.root.join('javascripts')
# For sprockets see https://ddnexus.github.io/pagy/docs/api/javascript/setup#sprockets
# Rails.application.config.assets.precompile += %w( pagy.js )

# I18n
# See https://ddnexus.github.io/pagy/docs/api/i18n
# Pagy::I18n.load(locale: 'de')
# Pagy::I18n.load(locale: 'de', filepath: 'path/to/pagy-de.yml')
# Pagy::I18n.load(locale: 'de', locale: 'en')
# Pagy::I18n.load(locale: 'de', locale: 'en', filepath: 'path/to/pagy-de.yml')

# When you are done setting your own default freeze it, so it will not get changed accidentally
Pagy::DEFAULT.freeze