class HotelAmenityBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :amenity_id, :is_available, :is_free, :location, :notes, :operating_hours, :created_at, :updated_at
  
  field :additional_cost do |hotel_amenity|
    hotel_amenity.additional_cost&.to_s
  end
  
  association :amenity, blueprint: AmenityBlueprint
end