class GuestBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :first_name, :last_name, :email, :phone, :date_of_birth, :nationality,
         :address, :city, :state, :country, :postal_code, :vip_status, :notes
  
  field :full_name do |guest|
    guest.full_name
  end
end