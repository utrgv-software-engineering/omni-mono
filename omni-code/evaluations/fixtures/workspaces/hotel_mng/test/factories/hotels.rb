# == Schema Information
#
# Table name: hotels
#
#  id             :bigint           not null, primary key
#  address        :string           default(""), not null
#  check_in_time  :time             default(2000-01-01 15:00:00.000000000 UTC +00:00)
#  check_out_time :time             default(2000-01-01 11:00:00.000000000 UTC +00:00)
#  city           :string           default(""), not null
#  country        :string           default(""), not null
#  description    :text             default("")
#  email          :string           default(""), not null
#  name           :string
#  phone          :string           default(""), not null
#  postal_code    :string           default(""), not null
#  star_rating    :decimal(2, 1)
#  state          :string           default(""), not null
#  subdomain      :string
#  timezone       :string           default("UTC"), not null
#  total_rooms    :integer          default(0)
#  website        :string           default("")
#  created_at     :datetime         not null
#  updated_at     :datetime         not null
#  account_id     :bigint           not null
#
# Indexes
#
#  index_hotels_on_account_id                (account_id)
#  index_hotels_on_account_id_and_subdomain  (account_id,subdomain) UNIQUE
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
FactoryBot.define do
  factory :hotel do
    association :account
    sequence(:name) { |n| "Hotel #{n}" }
    sequence(:subdomain) { |n| "hotel#{n}" }
    address { "456 Hotel Ave" }
    city { "San Francisco" }
    state { "CA" }
    country { "US" }
    postal_code { "94102" }
    phone { "555-0200" }
    sequence(:email) { |n| "contact#{n}@hotel.com" }
    website { "https://www.hotel.com" }
    description { "A wonderful hotel experience" }
    check_in_time { "15:00" }
    check_out_time { "11:00" }
    timezone { "Pacific Time (US & Canada)" }
    star_rating { 4.5 }
    total_rooms { 100 }
  end
end
