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
class MaintenanceRequest < ApplicationRecord
  acts_as_tenant(:account)
  
  belongs_to :account
  belongs_to :hotel
  belongs_to :room, optional: true
  belongs_to :reported_by, class_name: 'User'
  belongs_to :assigned_to, class_name: 'User', optional: true
  
  CATEGORIES = %w[electrical plumbing hvac furniture other].freeze
  PRIORITIES = %w[low normal high critical].freeze
  STATUSES = %w[open in_progress pending_parts completed cancelled].freeze
  
  validates :category, presence: true, inclusion: { in: CATEGORIES }
  validates :issue_description, presence: true
  validates :priority, inclusion: { in: PRIORITIES }
  validates :status, inclusion: { in: STATUSES }
  validates :cost_estimate, numericality: { greater_than_or_equal_to: 0 }, allow_nil: true
  validates :actual_cost, numericality: { greater_than_or_equal_to: 0 }, allow_nil: true
  
  scope :open, -> { where(status: 'open') }
  scope :in_progress, -> { where(status: 'in_progress') }
  scope :pending_parts, -> { where(status: 'pending_parts') }
  scope :completed, -> { where(status: 'completed') }
  scope :cancelled, -> { where(status: 'cancelled') }
  scope :active, -> { where.not(status: ['completed', 'cancelled']) }
  scope :by_priority, -> { order(Arel.sql("CASE priority 
                                            WHEN 'critical' THEN 1 
                                            WHEN 'high' THEN 2 
                                            WHEN 'normal' THEN 3 
                                            WHEN 'low' THEN 4 
                                            END")) }
  scope :overdue, -> { active.where('scheduled_date < ?', Date.current) }
  scope :unassigned, -> { where(assigned_to: nil) }
  scope :for_room, ->(room) { where(room: room) }
  scope :for_common_areas, -> { where(room: nil) }
  
  def open?
    status == 'open'
  end
  
  def in_progress?
    status == 'in_progress'
  end
  
  def pending_parts?
    status == 'pending_parts'
  end
  
  def completed?
    status == 'completed'
  end
  
  def cancelled?
    status == 'cancelled'
  end
  
  def active?
    !completed? && !cancelled?
  end
  
  def critical?
    priority == 'critical'
  end
  
  def high_priority?
    priority == 'high'
  end
  
  def overdue?
    active? && scheduled_date.present? && scheduled_date < Date.current
  end
  
  def start_work!
    update!(status: 'in_progress')
  end
  
  def await_parts!
    update!(status: 'pending_parts')
  end
  
  def complete!(resolution_notes: nil, actual_cost: nil)
    attrs = { status: 'completed', completed_at: Time.current }
    attrs[:resolution_notes] = resolution_notes if resolution_notes.present?
    attrs[:actual_cost] = actual_cost if actual_cost.present?
    update!(attrs)
  end
  
  def cancel!(reason = nil)
    attrs = { status: 'cancelled' }
    attrs[:resolution_notes] = reason if reason.present?
    update!(attrs)
  end
  
  def cost_variance
    return nil unless cost_estimate && actual_cost
    actual_cost - cost_estimate
  end
  
  def cost_variance_percentage
    return nil unless cost_estimate && actual_cost && cost_estimate > 0
    ((actual_cost - cost_estimate) / cost_estimate * 100).round(1)
  end
  
  def days_open
    end_date = completed_at || Time.current
    ((end_date - created_at) / 1.day).round
  end
  
  def location_description
    room ? "Room #{room.room_number}" : "Common Area"
  end
end
