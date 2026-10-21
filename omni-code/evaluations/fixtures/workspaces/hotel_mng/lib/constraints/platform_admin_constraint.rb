# Routing constraint to restrict access to platform admins only
# Used to protect mounted engines like Mission Control Jobs
module Constraints
  class PlatformAdminConstraint
    def self.matches?(request)
      return false unless request.env["warden"].authenticated?(:user)

      user = request.env["warden"].user(:user)
      user&.platform_admin?
    end
  end
end
