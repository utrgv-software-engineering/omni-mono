# == Schema Information
#
# Table name: service_bookings
#
#  id               :bigint           not null, primary key
#  booking_date     :date             not null
#  booking_time     :time
#  duration_minutes :integer
#  notes            :text
#  quantity         :integer          default(1)
#  status           :string           default("pending")
#  total_price      :decimal(10, 2)   not null
#  unit_price       :decimal(10, 2)   not null
#  created_at       :datetime         not null
#  updated_at       :datetime         not null
#  account_id       :bigint           not null
#  guest_id         :bigint           not null
#  reservation_id   :bigint
#  service_id       :bigint           not null
#
# Indexes
#
#  idx_on_account_id_booking_date_status_e0ad81b3d5      (account_id,booking_date,status)
#  idx_on_account_id_service_id_booking_date_e8b5f59c22  (account_id,service_id,booking_date)
#  index_service_bookings_on_account_id                  (account_id)
#  index_service_bookings_on_guest_id                    (guest_id)
#  index_service_bookings_on_reservation_id              (reservation_id)
#  index_service_bookings_on_service_id                  (service_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (guest_id => guests.id)
#  fk_rails_...  (reservation_id => reservations.id)
#  fk_rails_...  (service_id => services.id)
#
FactoryBot.define do
  factory :service_booking do
    association :account
    association :service
    association :guest
    association :reservation
    booking_date { Date.current + 1.day }
    booking_time { "10:00" }
    duration_minutes { 60 }
    quantity { 1 }
    unit_price { 50.00 }
    total_price { 50.00 }
    status { 'pending' }
    notes { nil }

    trait :confirmed do
      status { 'confirmed' }
    end

    trait :completed do
      status { 'completed' }
      booking_date { Date.current - 1.day }
    end

    trait :cancelled do
      status { 'cancelled' }
    end

    trait :without_reservation do
      reservation { nil }
    end

    trait :spa_booking do
      association :service, factory: [:service, :spa]
      unit_price { 150.00 }
      total_price { 150.00 }
      duration_minutes { 90 }
    end

    trait :dining_booking do
      association :service, factory: [:service, :dining]
      quantity { 2 }
      unit_price { 75.00 }
      total_price { 150.00 }
    end

    trait :multiple_quantity do
      quantity { 3 }
      total_price { 150.00 }
    end

    trait :with_notes do
      notes { "Special dietary requirements: vegetarian" }
    end

    trait :future do
      booking_date { Date.current + 7.days }
    end

    trait :past do
      booking_date { Date.current - 7.days }
      status { 'completed' }
    end
  end
end
