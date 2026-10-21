class ApplicationBlueprint < Blueprinter::Base
  # Configure Blueprinter to use OJ for JSON serialization
  Blueprinter.configure do |config|
    config.generator = Oj
  end
end