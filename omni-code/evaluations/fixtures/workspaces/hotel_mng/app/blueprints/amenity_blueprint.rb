class AmenityBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :name, :category, :icon, :description, :is_countable, :created_at, :updated_at
end