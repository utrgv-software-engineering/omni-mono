class ServiceBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :name, :category, :description, :pricing_unit, 
         :duration_minutes, :capacity, :advance_booking_required, 
         :is_active, :created_at, :updated_at
  
  field :price do |service|
    service.price.to_s
  end
  
  view :basic do
    fields :id, :name, :category, :is_active
    
    field :price do |service|
      service.price.to_s
    end
  end
end