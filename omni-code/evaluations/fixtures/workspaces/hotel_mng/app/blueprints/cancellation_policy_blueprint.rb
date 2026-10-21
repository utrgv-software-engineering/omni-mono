class CancellationPolicyBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :name, :code, :cancellation_type, :description, :is_refundable, :is_default,
         :free_cancellation_hours, :penalty_nights, :hotel_id, :created_at, :updated_at
  
  field :penalty_percentage do |policy|
    policy.penalty_percentage&.to_s
  end
  
  field :no_show_penalty do |policy|
    policy.no_show_penalty&.to_s
  end
  
  field :scope do |policy|
    policy.hotel_id.nil? ? 'account_wide' : 'hotel_specific'
  end
end