# == Schema Information
#
# Table name: service_schedules
#
#  id                    :bigint           not null, primary key
#  day_of_week           :integer          not null
#  end_time              :time             not null
#  max_bookings_per_slot :integer
#  start_time            :time             not null
#  created_at            :datetime         not null
#  updated_at            :datetime         not null
#  account_id            :bigint           not null
#  service_id            :bigint           not null
#
# Indexes
#
#  idx_on_account_id_service_id_day_of_week_fa26cada02  (account_id,service_id,day_of_week)
#  index_service_schedules_on_account_id                (account_id)
#  index_service_schedules_on_service_id                (service_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (service_id => services.id)
#

FactoryBot.define do
  factory :service_schedule do
    association :account
    association :service
    day_of_week { 1 }
    start_time { "09:00" }
    end_time { "17:00" }
    max_bookings_per_slot { 5 }

    trait :monday do
      day_of_week { 1 }
    end

    trait :tuesday do
      day_of_week { 2 }
    end

    trait :wednesday do
      day_of_week { 3 }
    end

    trait :thursday do
      day_of_week { 4 }
    end

    trait :friday do
      day_of_week { 5 }
    end

    trait :saturday do
      day_of_week { 6 }
    end

    trait :sunday do
      day_of_week { 0 }
    end

    trait :morning do
      start_time { "06:00" }
      end_time { "12:00" }
    end

    trait :afternoon do
      start_time { "12:00" }
      end_time { "18:00" }
    end

    trait :evening do
      start_time { "18:00" }
      end_time { "23:00" }
    end

    trait :all_day do
      start_time { "00:00" }
      end_time { "23:59" }
    end

    trait :unlimited_capacity do
      max_bookings_per_slot { nil }
    end

    trait :limited_capacity do
      max_bookings_per_slot { 1 }
    end
  end
end
