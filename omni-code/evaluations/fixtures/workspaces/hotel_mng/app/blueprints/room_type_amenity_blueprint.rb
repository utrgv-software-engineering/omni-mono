class RoomTypeAmenityBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :amenity_id, :quantity, :created_at, :updated_at
  
  association :amenity, blueprint: AmenityBlueprint
  
  field :display_text do |room_type_amenity|
    room_type_amenity.display_text
  end
end