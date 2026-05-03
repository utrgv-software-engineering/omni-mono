# == Schema Information
#
# Table name: reservations
#
#  id                  :bigint           not null, primary key
#  actual_check_in     :datetime
#  actual_check_out    :datetime
#  adults              :integer          default(1)
#  check_in_date       :date             not null
#  check_out_date      :date             not null
#  children            :integer          default(0)
#  confirmation_number :string           not null
#  currency            :string           default("USD")
#  infants             :integer          default(0)
#  internal_notes      :text
#  paid_amount         :decimal(10, 2)   default(0.0)
#  payment_status      :string           default("pending")
#  source              :string
#  special_requests    :text
#  status              :string           default("pending")
#  total_amount        :decimal(10, 2)   not null
#  created_at          :datetime         not null
#  updated_at          :datetime         not null
#  account_id          :bigint           not null
#  guest_id            :bigint           not null
#  hotel_id            :bigint           not null
#  room_id             :bigint
#  room_type_id        :bigint           not null
#
# Indexes
#
#  idx_on_account_id_hotel_id_check_in_date_7087d23249       (account_id,hotel_id,check_in_date)
#  index_reservations_on_account_id                          (account_id)
#  index_reservations_on_account_id_and_confirmation_number  (account_id,confirmation_number) UNIQUE
#  index_reservations_on_account_id_and_hotel_id_and_status  (account_id,hotel_id,status)
#  index_reservations_on_guest_id                            (guest_id)
#  index_reservations_on_hotel_id                            (hotel_id)
#  index_reservations_on_room_id                             (room_id)
#  index_reservations_on_room_type_id                        (room_type_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (guest_id => guests.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (room_id => rooms.id)
#  fk_rails_...  (room_type_id => room_types.id)
#
FactoryBot.define do
  factory :reservation do
    association :account
    association :hotel
    association :guest
    association :room_type
    room { nil } # Room can be assigned later
    confirmation_number { nil } # Auto-generated
    check_in_date { Date.tomorrow }
    check_out_date { 3.days.from_now }
    actual_check_in { nil }
    actual_check_out { nil }
    status { "pending" }
    adults { 2 }
    children { 0 }
    infants { 0 }
    total_amount { 450.00 }
    paid_amount { 0.00 }
    currency { "USD" }
    payment_status { "pending" }
    special_requests { nil }
    internal_notes { nil }
    source { "website" }
    
    trait :confirmed do
      status { "confirmed" }
      association :room
    end
    
    trait :checked_in do
      status { "checked_in" }
      actual_check_in { Time.current }
      association :room
    end
    
    trait :checked_out do
      status { "checked_out" }
      actual_check_in { 2.days.ago }
      actual_check_out { Time.current }
      payment_status { "paid" }
      paid_amount { 450.00 }
      association :room
    end
    
    trait :cancelled do
      status { "cancelled" }
    end
    
    trait :paid do
      payment_status { "paid" }
      paid_amount { 450.00 }
    end
  end
end
