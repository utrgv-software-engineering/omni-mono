class HousekeepingTaskBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :task_type, :priority, :status, :scheduled_date,
         :started_at, :completed_at, :inspection_score, :notes,
         :hotel_id, :room_id, :assigned_to_id
  
  field :scheduled_time do |task|
    task.scheduled_time&.strftime('%H:%M') if task.scheduled_time
  end
  
  association :room, blueprint: RoomBlueprint
  
  # Include assigned user info
  field :assigned_to do |task|
    if task.assigned_to
      {
        id: task.assigned_to.id,
        email: task.assigned_to.email,
        first_name: task.assigned_to.first_name,
        last_name: task.assigned_to.last_name
      }
    end
  end
  
  # Computed fields
  field :is_overdue do |task|
    task.overdue?
  end
  
  field :duration_minutes do |task|
    task.duration_minutes
  end
  
  field :scheduled_datetime do |task|
    task.scheduled_datetime
  end
  
  # Detailed view with room details
  view :detailed do
    fields :task_type, :priority, :status, :scheduled_date,
           :started_at, :completed_at, :inspection_score, :notes,
           :hotel_id, :room_id, :assigned_to_id, :created_at, :updated_at
    
    field :scheduled_time do |task|
      task.scheduled_time&.strftime('%H:%M') if task.scheduled_time
    end
    
    association :room, blueprint: RoomBlueprint
    
    field :assigned_to do |task|
      if task.assigned_to
        {
          id: task.assigned_to.id,
          email: task.assigned_to.email,
          first_name: task.assigned_to.first_name,
          last_name: task.assigned_to.last_name
        }
      end
    end
    
    field :is_overdue do |task|
      task.overdue?
    end
    
    field :duration_minutes do |task|
      task.duration_minutes
    end
    
    field :scheduled_datetime do |task|
      task.scheduled_datetime
    end
  end
end