class RoomTypeBlueprint < ApplicationBlueprint
  identifier :id
  
  view :basic do
    fields :name, :code
  end
  
  view :default do
    fields :name, :code, :description, :base_price, :standard_occupancy,
           :max_occupancy, :extra_person_charge, :size_sqm
  end
  
  view :with_availability do
    include_view :default
    field :available_count
    field :total_price
    field :nights
  end
end