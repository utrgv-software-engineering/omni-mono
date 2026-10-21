class AddDefaultsToHotelsOptionalFields < ActiveRecord::Migration[8.0]
  def change
    change_column_default :hotels, :total_rooms, from: nil, to: 0
    change_column_default :hotels, :website, from: nil, to: ''
    change_column_default :hotels, :description, from: nil, to: ''
  end
end
