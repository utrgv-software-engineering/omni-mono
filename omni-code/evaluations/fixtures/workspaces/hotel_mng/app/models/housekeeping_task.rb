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
class HousekeepingTask < ApplicationRecord
  acts_as_tenant(:account)
  
  belongs_to :account
  belongs_to :hotel
  belongs_to :room
  belongs_to :assigned_to, class_name: 'User', optional: true
  
  TASK_TYPES = %w[cleaning inspection turndown deep_clean].freeze
  PRIORITIES = %w[low normal high urgent].freeze
  STATUSES = %w[pending in_progress completed cancelled].freeze
  
  validates :task_type, presence: true, inclusion: { in: TASK_TYPES }
  validates :priority, inclusion: { in: PRIORITIES }
  validates :status, inclusion: { in: STATUSES }
  validates :scheduled_date, presence: true
  validates :inspection_score, numericality: { in: 1..10 }, allow_nil: true
  
  scope :pending, -> { where(status: 'pending') }
  scope :in_progress, -> { where(status: 'in_progress') }
  scope :completed, -> { where(status: 'completed') }
  scope :cancelled, -> { where(status: 'cancelled') }
  scope :by_priority, -> { order(Arel.sql("CASE priority 
                                            WHEN 'urgent' THEN 1 
                                            WHEN 'high' THEN 2 
                                            WHEN 'normal' THEN 3 
                                            WHEN 'low' THEN 4 
                                            END")) }
  scope :for_date, ->(date) { where(scheduled_date: date) }
  scope :overdue, -> { pending.where('scheduled_date < ?', Date.current) }
  scope :today, -> { for_date(Date.current) }
  scope :upcoming, -> { where('scheduled_date >= ?', Date.current).order(:scheduled_date, :scheduled_time) }
  
  def pending?
    status == 'pending'
  end
  
  def in_progress?
    status == 'in_progress'
  end
  
  def completed?
    status == 'completed'
  end
  
  def cancelled?
    status == 'cancelled'
  end
  
  def urgent?
    priority == 'urgent'
  end
  
  def high_priority?
    priority == 'high'
  end
  
  def overdue?
    pending? && scheduled_date < Date.current
  end
  
  def start!
    update!(status: 'in_progress', started_at: Time.current)
  end
  
  def complete!(score = nil)
    attrs = { status: 'completed', completed_at: Time.current }
    attrs[:inspection_score] = score if score.present?
    update!(attrs)
  end
  
  def cancel!
    update!(status: 'cancelled')
  end
  
  def duration_minutes
    return nil unless started_at && completed_at
    ((completed_at - started_at) / 60).round
  end
  
  def scheduled_datetime
    return scheduled_date.to_datetime unless scheduled_time
    scheduled_date.to_datetime + scheduled_time.seconds_since_midnight.seconds
  end
end
