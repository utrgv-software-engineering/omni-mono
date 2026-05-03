# frozen_string_literal: true

class ApplicationPolicy
  attr_reader :user, :record

  def initialize(user, record)
    @user = user
    @record = record
  end

  def index?
    false
  end

  def show?
    false
  end

  def create?
    false
  end

  def new?
    create?
  end

  def update?
    false
  end

  def edit?
    update?
  end

  def destroy?
    false
  end

  class Scope
    def initialize(user, scope)
      @user = user
      @scope = scope
    end

    def resolve
      raise NoMethodError, "You must define #resolve in #{self.class}"
    end

    private

    attr_reader :user, :scope
    
    # Since we're now passing AccountUser as the pundit_user, 
    # user is already the AccountUser object
    def account_user
      user
    end

    def owner?
      account_user&.has_role?(:owner)
    end

    def admin?
      account_user&.has_role?(:admin)
    end

    def hotel_manager?
      account_user&.has_role?(:hotel_manager)
    end

    def front_desk?
      account_user&.has_role?(:front_desk)
    end

    def housekeeping?
      account_user&.has_role?(:housekeeping)
    end
    
    def front_desk_or_above?
      owner? || admin? || hotel_manager? || front_desk?
    end
    
    def manager_or_above?
      owner? || admin? || hotel_manager?
    end
    
    def any_staff?
      owner? || admin? || hotel_manager? || front_desk? || housekeeping?
    end
    
    def owner_or_admin?
      owner? || admin?
    end
  end
  
  private
  
  # Since we're now passing AccountUser as the pundit_user, 
  # user is already the AccountUser object
  def account_user
    user
  end

  def owner?
    account_user&.has_role?(:owner)
  end

  def admin?
    account_user&.has_role?(:admin)
  end

  def hotel_manager?
    account_user&.has_role?(:hotel_manager)
  end

  def front_desk?
    account_user&.has_role?(:front_desk)
  end

  def housekeeping?
    account_user&.has_role?(:housekeeping)
  end
  
  def owner_or_admin?
    owner? || admin?
  end
  
  def manager_or_above?
    owner? || admin? || hotel_manager?
  end
  
  def front_desk_or_above?
    owner? || admin? || hotel_manager? || front_desk?
  end
  
  def any_staff?
    owner? || admin? || hotel_manager? || front_desk? || housekeeping?
  end
end
