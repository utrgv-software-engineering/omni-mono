# == Schema Information
#
# Table name: housekeeping_tasks
#
#  id               :bigint           not null, primary key
#  completed_at     :datetime
#  inspection_score :integer
#  notes            :text
#  priority         :string           default("normal")
#  scheduled_date   :date             not null
#  scheduled_time   :time
#  started_at       :datetime
#  status           :string           default("pending")
#  task_type        :string           not null
#  created_at       :datetime         not null
#  updated_at       :datetime         not null
#  account_id       :bigint           not null
#  assigned_to_id   :bigint
#  hotel_id         :bigint           not null
#  room_id          :bigint           not null
#
# Indexes
#
#  index_housekeeping_tasks_on_account_hotel_date_status  (account_id,hotel_id,scheduled_date,status)
#  index_housekeeping_tasks_on_account_room_status        (account_id,room_id,status)
#  index_housekeeping_tasks_on_assigned_to_id             (assigned_to_id)
#  index_housekeeping_tasks_on_hotel_id                   (hotel_id)
#  index_housekeeping_tasks_on_room_id                    (room_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (assigned_to_id => users.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (room_id => rooms.id)
#
FactoryBot.define do
  factory :housekeeping_task do
    association :account
    association :hotel
    association :room
    association :assigned_to, factory: :user
    task_type { 'cleaning' }
    priority { 'normal' }
    status { 'pending' }
    scheduled_date { Date.current }
    scheduled_time { '09:00' }
    notes { 'Regular cleaning' }
    
    trait :inspection do
      task_type { 'inspection' }
      notes { 'Quality inspection' }
    end
    
    trait :turndown do
      task_type { 'turndown' }
      scheduled_time { '18:00' }
      notes { 'Evening turndown service' }
    end
    
    trait :deep_clean do
      task_type { 'deep_clean' }
      notes { 'Deep cleaning scheduled' }
    end
    
    trait :urgent do
      priority { 'urgent' }
    end
    
    trait :high_priority do
      priority { 'high' }
    end
    
    trait :low_priority do
      priority { 'low' }
    end
    
    trait :in_progress do
      status { 'in_progress' }
      started_at { 1.hour.ago }
    end
    
    trait :completed do
      status { 'completed' }
      started_at { 2.hours.ago }
      completed_at { 30.minutes.ago }
      inspection_score { 9 }
    end
    
    trait :cancelled do
      status { 'cancelled' }
    end
    
    trait :overdue do
      scheduled_date { 2.days.ago }
      status { 'pending' }
    end
    
    trait :unassigned do
      assigned_to { nil }
    end
  end
end
