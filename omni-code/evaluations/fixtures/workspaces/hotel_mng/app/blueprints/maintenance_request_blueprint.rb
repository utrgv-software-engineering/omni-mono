class MaintenanceRequestBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :category, :issue_description, :priority, :status,
         :scheduled_date, :cost_estimate, :actual_cost,
         :resolution_notes, :completed_at,
         :hotel_id, :room_id, :reported_by_id, :assigned_to_id,
         :created_at, :updated_at
  
  # Room association - only include if present
  field :room, if: ->(field_name, request, options) { request.room.present? } do |request|
    RoomBlueprint.render_as_hash(request.room)
  end
  
  # Include reported_by user info
  field :reported_by do |request|
    {
      id: request.reported_by.id,
      email: request.reported_by.email,
      first_name: request.reported_by.first_name,
      last_name: request.reported_by.last_name
    }
  end
  
  # Include assigned_to user info
  field :assigned_to do |request|
    if request.assigned_to
      {
        id: request.assigned_to.id,
        email: request.assigned_to.email,
        first_name: request.assigned_to.first_name,
        last_name: request.assigned_to.last_name
      }
    end
  end
  
  # Computed fields
  field :location_description do |request|
    request.location_description
  end
  
  field :days_open do |request|
    request.persisted? ? request.days_open : 0
  end
  
  field :cost_variance do |request|
    request.cost_variance&.to_s
  end
  
  field :cost_variance_percentage do |request|
    request.cost_variance_percentage&.to_f
  end
end