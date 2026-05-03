# == Schema Information
#
# Table name: guests
#
#  id              :bigint           not null, primary key
#  address         :string
#  city            :string
#  country         :string
#  date_of_birth   :date
#  email           :string           not null
#  first_name      :string           not null
#  id_number       :string
#  last_name       :string           not null
#  nationality     :string
#  notes           :text
#  passport_number :string
#  phone           :string
#  postal_code     :string
#  state           :string
#  vip_status      :boolean          default(FALSE)
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#
# Indexes
#
#  index_guests_on_account_id                (account_id)
#  index_guests_on_account_id_and_email      (account_id,email)
#  index_guests_on_account_id_and_last_name  (account_id,last_name)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
FactoryBot.define do
  factory :guest do
    association :account
    first_name { "John" }
    last_name { "Doe" }
    sequence(:email) { |n| "guest#{n}@example.com" }
    phone { "555-0123" }
    date_of_birth { 30.years.ago }
    nationality { "US" }
    passport_number { "A12345678" }
    id_number { "123-45-6789" }
    address { "789 Guest St" }
    city { "Los Angeles" }
    state { "CA" }
    country { "US" }
    postal_code { "90001" }
    vip_status { false }
    notes { nil }
    
    trait :vip do
      vip_status { true }
      notes { "VIP guest - special attention required" }
    end
    
    trait :international do
      nationality { "GB" }
      passport_number { "GB9876543" }
      country { "GB" }
      city { "London" }
      state { nil }
      postal_code { "SW1A 1AA" }
    end
  end
end
