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
require "test_helper"

class HousekeepingTaskTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account)
    @user = create(:user)
    @task = create(:housekeeping_task, 
                   hotel: @hotel, 
                   room: @room, 
                   account: @account,
                   assigned_to: @user)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "start! transitions task to in_progress and sets started_at" do
    assert @task.pending?
    assert_nil @task.started_at
    
    @task.start!
    
    assert @task.in_progress?
    assert_not_nil @task.started_at
    assert_in_delta Time.current, @task.started_at, 2.seconds
  end
  
  test "complete! transitions task to completed and sets completed_at" do
    @task.start!
    assert @task.in_progress?
    
    @task.complete!(9)
    
    assert @task.completed?
    assert_not_nil @task.completed_at
    assert_equal 9, @task.inspection_score
    assert_in_delta Time.current, @task.completed_at, 2.seconds
  end
  
  test "complete! without score sets status but not score" do
    @task.start!
    @task.complete!
    
    assert @task.completed?
    assert_nil @task.inspection_score
  end
  
  test "cancel! transitions task to cancelled" do
    assert @task.pending?
    
    @task.cancel!
    
    assert @task.cancelled?
  end
  
  test "duration_minutes calculates time between start and completion" do
    @task.started_at = 2.hours.ago
    @task.completed_at = 30.minutes.ago
    
    assert_equal 90, @task.duration_minutes
  end
  
  test "duration_minutes returns nil when not started or completed" do
    assert_nil @task.duration_minutes
    
    @task.started_at = 1.hour.ago
    assert_nil @task.duration_minutes
  end
  
  test "overdue? returns true for pending tasks with past scheduled date" do
    overdue_task = create(:housekeeping_task, :overdue, 
                          hotel: @hotel, 
                          room: @room, 
                          account: @account)
    
    assert overdue_task.overdue?
  end
  
  test "overdue? returns false for completed tasks" do
    overdue_task = create(:housekeeping_task, :completed, 
                          hotel: @hotel, 
                          room: @room, 
                          account: @account,
                          scheduled_date: 3.days.ago)
    
    assert_not overdue_task.overdue?
  end
  
  test "scheduled_datetime combines date and time correctly" do
    task = create(:housekeeping_task,
                  hotel: @hotel,
                  room: @room,
                  account: @account,
                  scheduled_date: Date.new(2025, 8, 25),
                  scheduled_time: Time.zone.parse("14:30:00"))
    
    expected = DateTime.new(2025, 8, 25, 14, 30, 0, Time.zone.formatted_offset)
    assert_equal expected, task.scheduled_datetime
  end
  
  test "scheduled_datetime handles missing time" do
    task = create(:housekeeping_task,
                  hotel: @hotel,
                  room: @room,
                  account: @account,
                  scheduled_date: Date.new(2025, 8, 25),
                  scheduled_time: nil)
    
    expected = Date.new(2025, 8, 25).to_datetime
    assert_equal expected, task.scheduled_datetime
  end
  
  test "by_priority scope orders tasks correctly" do
    urgent = create(:housekeeping_task, :urgent, hotel: @hotel, room: @room, account: @account)
    high = create(:housekeeping_task, :high_priority, hotel: @hotel, room: @room, account: @account)
    low = create(:housekeeping_task, :low_priority, hotel: @hotel, room: @room, account: @account)
    
    ordered = HousekeepingTask.by_priority
    
    assert_equal 'urgent', ordered.first.priority
    assert_equal 'low', ordered.last.priority
  end
end
