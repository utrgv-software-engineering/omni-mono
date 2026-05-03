# == Schema Information
#
# Table name: maintenance_requests
#
#  id                :bigint           not null, primary key
#  actual_cost       :decimal(10, 2)
#  category          :string           not null
#  completed_at      :datetime
#  cost_estimate     :decimal(10, 2)
#  issue_description :text             not null
#  priority          :string           default("normal")
#  resolution_notes  :text
#  scheduled_date    :date
#  status            :string           default("open")
#  created_at        :datetime         not null
#  updated_at        :datetime         not null
#  account_id        :bigint           not null
#  assigned_to_id    :bigint
#  hotel_id          :bigint           not null
#  reported_by_id    :bigint           not null
#  room_id           :bigint
#
# Indexes
#
#  index_maintenance_requests_on_account_hotel_status  (account_id,hotel_id,status)
#  index_maintenance_requests_on_account_room_status   (account_id,room_id,status)
#  index_maintenance_requests_on_assigned_to_id        (assigned_to_id)
#  index_maintenance_requests_on_hotel_id              (hotel_id)
#  index_maintenance_requests_on_reported_by_id        (reported_by_id)
#  index_maintenance_requests_on_room_id               (room_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (assigned_to_id => users.id)
#  fk_rails_...  (hotel_id => hotels.id)
#  fk_rails_...  (reported_by_id => users.id)
#  fk_rails_...  (room_id => rooms.id)
#
FactoryBot.define do
  factory :maintenance_request do
    association :account
    association :hotel
    association :room
    association :reported_by, factory: :user
    association :assigned_to, factory: :user
    category { 'electrical' }
    issue_description { 'Light switch not working in room' }
    priority { 'normal' }
    status { 'open' }
    cost_estimate { 150.00 }
    scheduled_date { Date.tomorrow }
    
    trait :plumbing do
      category { 'plumbing' }
      issue_description { 'Leaking faucet in bathroom' }
      cost_estimate { 200.00 }
    end
    
    trait :hvac do
      category { 'hvac' }
      issue_description { 'Air conditioning not cooling properly' }
      cost_estimate { 350.00 }
    end
    
    trait :furniture do
      category { 'furniture' }
      issue_description { 'Broken chair leg' }
      cost_estimate { 75.00 }
    end
    
    trait :other do
      category { 'other' }
      issue_description { 'Window blind stuck' }
      cost_estimate { 50.00 }
    end
    
    trait :critical do
      priority { 'critical' }
      issue_description { 'Electrical hazard - exposed wiring' }
    end
    
    trait :high_priority do
      priority { 'high' }
    end
    
    trait :low_priority do
      priority { 'low' }
    end
    
    trait :in_progress do
      status { 'in_progress' }
    end
    
    trait :pending_parts do
      status { 'pending_parts' }
    end
    
    trait :completed do
      status { 'completed' }
      completed_at { 1.hour.ago }
      actual_cost { 145.00 }
      resolution_notes { 'Issue resolved successfully' }
    end
    
    trait :cancelled do
      status { 'cancelled' }
      resolution_notes { 'Request cancelled by hotel manager' }
    end
    
    trait :common_area do
      room { nil }
      issue_description { 'Lobby lighting needs repair' }
    end
    
    trait :unassigned do
      assigned_to { nil }
    end
    
    trait :overdue do
      scheduled_date { 3.days.ago }
      status { 'open' }
    end
  end
end
