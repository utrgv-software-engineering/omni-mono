# Service class for managing AI agent deployments configuration
class AiDeployments
  def self.config
    @config ||= YAML.load(
      ERB.new(File.read(Rails.root.join('config/ai_deployments.yml'))).result
    ).deep_symbolize_keys
  end
  
  # Platform deployments (always enabled)
  def self.platform_deployments
    config[:platform] || {}
  end
  
  # Hotel deployments (hotel-controlled)
  def self.hotel_deployments
    config[:hotel] || {}
  end
  
  # Get a specific platform deployment
  def self.get_platform(deployment_key)
    platform_deployments[deployment_key.to_sym]
  end
  
  # Get a specific hotel deployment
  def self.get_hotel(deployment_key)
    hotel_deployments[deployment_key.to_sym]
  end
  
  # Check if platform deployment exists (always enabled if exists)
  def self.platform_enabled?(deployment_key)
    get_platform(deployment_key).present?
  end
  
  # Check if hotel deployment is enabled for a specific hotel
  def self.hotel_enabled?(hotel, deployment_key)
    return false unless get_hotel(deployment_key)
    
    hotel.hotel_ai_settings
      .where(deployment_key: deployment_key, enabled: true)
      .exists?
  end
end