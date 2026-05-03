class RoomBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :room_number, :floor, :status, :is_smoking, :notes,
         :room_type_id, :hotel_id
  
  field :last_cleaned_at do |room|
    room.last_cleaned_at&.iso8601
  end
  
  association :room_type, blueprint: RoomTypeBlueprint, view: :basic
end