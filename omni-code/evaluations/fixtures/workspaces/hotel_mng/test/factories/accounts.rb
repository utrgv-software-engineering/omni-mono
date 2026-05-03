# == Schema Information
#
# Table name: accounts
#
#  id            :bigint           not null, primary key
#  address       :string
#  city          :string
#  country       :string
#  currency      :string           default("USD")
#  email         :string
#  name          :string
#  phone         :string
#  postal_code   :string
#  state         :string
#  status        :string           default("active")
#  subdomain     :string
#  timezone      :string           default("UTC")
#  trial_ends_at :datetime
#  created_at    :datetime         not null
#  updated_at    :datetime         not null
#
# Indexes
#
#  index_accounts_on_subdomain  (subdomain) UNIQUE
#
FactoryBot.define do
  factory :account do
    sequence(:name) { |n| "Hotel Chain #{n}" }
    sequence(:subdomain) { |n| "chain#{n}" }
    email { "admin@example.com" }
    address { "123 Main St" }
    city { "New York" }
    state { "NY" }
    country { "US" }
    postal_code { "10001" }
    phone { "555-0100" }
    timezone { "Eastern Time (US & Canada)" }
    currency { "USD" }
    status { "active" }
    trial_ends_at { 30.days.from_now }
  end
end
