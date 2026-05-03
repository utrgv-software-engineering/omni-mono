class HotelBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
         :phone, :email, :description, :check_in_time, :check_out_time,
         :timezone, :star_rating, :total_rooms
  
  field :website do |hotel|
    hotel.website.presence
  end
  
  field :check_in_time do |hotel|
    hotel.check_in_time&.strftime('%H:%M')
  end
  
  field :check_out_time do |hotel|
    hotel.check_out_time&.strftime('%H:%M')
  end
  
  # Conditional associations - only included when specifically requested
  # These are loaded via the controller's include parameter
  view :with_amenities do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :hotel_amenities, blueprint: HotelAmenityBlueprint do |hotel|
      hotel.hotel_amenities.includes(:amenity).where(is_available: true)
    end
  end
  
  view :with_policies do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :hotel_policies, blueprint: HotelPolicyBlueprint do |hotel|
      hotel.hotel_policies.where(is_active: true).order(:sort_order)
    end
  end
  
  view :with_services do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :services, blueprint: ServiceBlueprint do |hotel|
      hotel.services.where(is_active: true).order(:category, :name)
    end
  end
  
  view :with_amenities_and_services do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :hotel_amenities, blueprint: HotelAmenityBlueprint do |hotel|
      hotel.hotel_amenities.includes(:amenity).where(is_available: true)
    end
    
    association :services, blueprint: ServiceBlueprint do |hotel|
      hotel.services.where(is_active: true).order(:category, :name)
    end
  end
  
  view :with_amenities_and_policies do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :hotel_amenities, blueprint: HotelAmenityBlueprint do |hotel|
      hotel.hotel_amenities.includes(:amenity).where(is_available: true)
    end
    
    association :hotel_policies, blueprint: HotelPolicyBlueprint do |hotel|
      hotel.hotel_policies.where(is_active: true).order(:sort_order)
    end
  end
  
  view :with_policies_and_services do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :hotel_policies, blueprint: HotelPolicyBlueprint do |hotel|
      hotel.hotel_policies.where(is_active: true).order(:sort_order)
    end
    
    association :services, blueprint: ServiceBlueprint do |hotel|
      hotel.services.where(is_active: true).order(:category, :name)
    end
  end
  
  view :detailed do
    fields :name, :subdomain, :address, :city, :state, :country, :postal_code,
           :phone, :email, :description, :check_in_time, :check_out_time,
           :timezone, :star_rating, :total_rooms
    
    field :website do |hotel|
      hotel.website.presence
    end
    
    field :check_in_time do |hotel|
      hotel.check_in_time&.strftime('%H:%M')
    end
    
    field :check_out_time do |hotel|
      hotel.check_out_time&.strftime('%H:%M')
    end
    
    association :hotel_amenities, blueprint: HotelAmenityBlueprint do |hotel|
      hotel.hotel_amenities.includes(:amenity).where(is_available: true)
    end
    
    association :hotel_policies, blueprint: HotelPolicyBlueprint do |hotel|
      hotel.hotel_policies.where(is_active: true).order(:sort_order)
    end
    
    association :services, blueprint: ServiceBlueprint do |hotel|
      hotel.services.where(is_active: true).order(:category, :name)
    end
  end
end
