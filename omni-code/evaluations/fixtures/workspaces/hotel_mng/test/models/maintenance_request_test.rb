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
require "test_helper"

class MaintenanceRequestTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account)
    @reporter = create(:user)
    @technician = create(:user)
    @request = create(:maintenance_request, 
                     hotel: @hotel, 
                     room: @room, 
                     account: @account,
                     reported_by: @reporter,
                     assigned_to: @technician,
                     cost_estimate: 200.00)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "start_work! transitions to in_progress" do
    assert @request.open?
    
    @request.start_work!
    
    assert @request.in_progress?
  end
  
  test "await_parts! transitions to pending_parts" do
    @request.start_work!
    
    @request.await_parts!
    
    assert @request.pending_parts?
  end
  
  test "complete! sets status, completed_at, and optional fields" do
    @request.start_work!
    
    @request.complete!(
      resolution_notes: "Replaced faulty switch",
      actual_cost: 185.50
    )
    
    assert @request.completed?
    assert_not_nil @request.completed_at
    assert_equal "Replaced faulty switch", @request.resolution_notes
    assert_equal 185.50, @request.actual_cost
    assert_in_delta Time.current, @request.completed_at, 2.seconds
  end
  
  test "complete! without optional parameters" do
    @request.complete!
    
    assert @request.completed?
    assert_not_nil @request.completed_at
  end
  
  test "cancel! sets status and optional reason" do
    @request.cancel!("No longer needed")
    
    assert @request.cancelled?
    assert_equal "No longer needed", @request.resolution_notes
  end
  
  test "active? returns true for non-completed/cancelled requests" do
    assert @request.active?
    
    @request.start_work!
    assert @request.active?
    
    @request.await_parts!
    assert @request.active?
  end
  
  test "active? returns false for completed requests" do
    @request.complete!
    assert_not @request.active?
  end
  
  test "active? returns false for cancelled requests" do
    @request.cancel!
    assert_not @request.active?
  end
  
  test "cost_variance calculates difference between actual and estimated cost" do
    @request.actual_cost = 225.00
    
    assert_equal 25.00, @request.cost_variance
  end
  
  test "cost_variance returns nil when costs not available" do
    @request.actual_cost = nil
    assert_nil @request.cost_variance
    
    @request.cost_estimate = nil
    @request.actual_cost = 100.00
    assert_nil @request.cost_variance
  end
  
  test "cost_variance_percentage calculates percentage difference" do
    @request.actual_cost = 250.00
    
    assert_equal 25.0, @request.cost_variance_percentage
  end
  
  test "cost_variance_percentage handles cost under estimate" do
    @request.actual_cost = 150.00
    
    assert_equal -25.0, @request.cost_variance_percentage
  end
  
  test "cost_variance_percentage returns nil for invalid data" do
    @request.actual_cost = nil
    assert_nil @request.cost_variance_percentage
    
    @request.cost_estimate = 0
    @request.actual_cost = 100.00
    assert_nil @request.cost_variance_percentage
  end
  
  test "days_open calculates days since creation" do
    @request.created_at = 5.days.ago
    
    assert_equal 5, @request.days_open
  end
  
  test "days_open uses completed_at when request is completed" do
    @request.created_at = 10.days.ago
    @request.completed_at = 3.days.ago
    
    assert_equal 7, @request.days_open
  end
  
  test "location_description returns room number when room present" do
    assert_equal "Room #{@room.room_number}", @request.location_description
  end
  
  test "location_description returns Common Area when no room" do
    @request.room = nil
    assert_equal "Common Area", @request.location_description
  end
  
  test "overdue? returns true for active requests past scheduled date" do
    overdue = create(:maintenance_request, :overdue,
                    hotel: @hotel,
                    account: @account,
                    reported_by: @reporter)
    
    assert overdue.overdue?
  end
  
  test "overdue? returns false for completed requests" do
    overdue = create(:maintenance_request, :completed,
                    hotel: @hotel,
                    account: @account,
                    reported_by: @reporter,
                    scheduled_date: 5.days.ago)
    
    assert_not overdue.overdue?
  end
  
  test "by_priority scope orders requests correctly" do
    critical = create(:maintenance_request, :critical, 
                     hotel: @hotel, account: @account, reported_by: @reporter)
    high = create(:maintenance_request, :high_priority, 
                 hotel: @hotel, account: @account, reported_by: @reporter)
    low = create(:maintenance_request, :low_priority, 
                hotel: @hotel, account: @account, reported_by: @reporter)
    
    ordered = MaintenanceRequest.by_priority
    
    assert_equal 'critical', ordered.first.priority
    assert_equal 'low', ordered.last.priority
  end
end
