# frozen_string_literal: true

class DashboardPolicy < Struct.new(:account_user, :dashboard)
  def show?
    # Any authenticated user with an account can view the dashboard
    account_user.present?
  end
end