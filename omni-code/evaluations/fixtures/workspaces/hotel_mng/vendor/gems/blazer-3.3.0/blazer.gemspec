# -*- encoding: utf-8 -*-

Gem::Specification.new do |s|
  s.name        = "blazer"
  s.version     = "3.3.0"
  s.authors     = ["Andrew Kane"]
  s.email       = ["andrew@chartkick.com"]
  s.summary     = "Explore your data with SQL. Easily create charts and dashboards, and share them with your team."
  s.description = "Explore your data with SQL. Easily create charts and dashboards, and share them with your team."
  s.homepage    = "https://github.com/ankane/blazer"
  s.license     = "MIT"

  s.files         = Dir["**/*"]
  s.require_paths = ["lib"]

  s.required_ruby_version = ">= 3.1"

  s.add_dependency "railties", ">= 6.1"
  s.add_dependency "activerecord", ">= 6.1"
  s.add_dependency "chartkick", ">= 5"
  s.add_dependency "safely_block", ">= 0.1.1"
end