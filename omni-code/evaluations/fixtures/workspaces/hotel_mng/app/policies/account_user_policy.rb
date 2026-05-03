# frozen_string_literal: true

class AccountUserPolicy < ApplicationPolicy
  def index?
    owner_or_admin?
  end

  def show?
    owner_or_admin?
  end

  def create?
    owner_or_admin?
  end

  def update?
    owner_or_admin?
  end

  def destroy?
    owner_or_admin? && record.user_id != user.id # Can't remove yourself
  end

  class Scope < ApplicationPolicy::Scope
    def resolve
      if owner_or_admin?
        scope.all
      else
        scope.none
      end
    end
  end
end