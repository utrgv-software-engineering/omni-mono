class HotelPolicyBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :policy_type, :title, :description, :sort_order, :is_active, :created_at, :updated_at
end