# The 37signals Guide to Rails Testing

## 1.0 Core Philosophy [CORE-PHIL]
The 37signals testing approach emphasizes testing through real interfaces over isolated units, favoring simplicity and business value over comprehensive coverage. The majority of testing happens through controller tests, with minimal unit tests and very focused system tests for smoke testing critical paths.

## 2.0 General Principles [GEN-PRIN]

### 2.1 Test Through HTTP When Possible [PRIN-HTTP]
- 2.1.1 Controller tests are your primary testing layer
- 2.1.2 Use real objects and database interactions
- 2.1.3 Avoid mocks and stubs
- 2.1.4 Test the application as users will experience it

### 2.2 Focus on Business Value [PRIN-VALUE]
- 2.2.1 Test behavior that matters to users and the business
- 2.2.2 Skip testing Rails defaults and basic functionality
- 2.2.3 Don't aim for 100% test coverage
- 2.2.4 Write tests that provide confidence, not compliance

### 2.3 Keep Tests Simple [PRIN-SIMPLE]
- 2.3.1 Avoid complex test helpers and abstractions
- 2.3.2 Use factories over fixtures
- 2.3.3 Keep setup minimal
- 2.3.4 Make test intentions clear through naming

## 3.0 Controller Tests (Primary Testing Layer) [CTRL-TEST]

### 3.1 Purpose [CTRL-PURPOSE]
Controller tests should be your primary testing layer, as they verify behavior through the same interface your users will use.

### 3.2 Key Aspects to Test [CTRL-ASPECTS]

#### 3.2.1 Resource Listing [CTRL-LIST]
- 3.2.1.1 Correct items are shown/hidden based on permissions
- 3.2.1.2 Pagination works if implemented
- 3.2.1.3 Search/filtering functions properly
- 3.2.1.4 Proper ordering of items

#### 3.2.2 Resource Creation [CTRL-CREATE]
- 3.2.2.1 Successful creation with valid parameters
- 3.2.2.2 Failed creation with invalid parameters
- 3.2.2.3 Proper association with current user
- 3.2.2.4 Correct redirect or response
- 3.2.2.5 Database record creation

#### 3.2.3 Resource Updates [CTRL-UPDATE]
- 3.2.3.1 Successful updates with valid parameters
- 3.2.3.2 Failed updates with invalid parameters
- 3.2.3.3 Authorization checks
- 3.2.3.4 State changes

#### 3.2.4 Resource Deletion [CTRL-DELETE]
- 3.2.4.1 Successful deletion
- 3.2.4.2 Authorization checks
- 3.2.4.3 Associated record cleanup
- 3.2.4.4 Soft deletion if implemented

### 3.3 Best Practices [CTRL-BEST]
- 3.3.1 Use `ActionDispatch::IntegrationTest`
- 3.3.2 Test both successful and failed scenarios
- 3.3.3 Verify database state changes
- 3.3.4 Check response status codes
- 3.3.5 Verify redirects and flash messages
- 3.3.6 Test HTML content when relevant
- 3.3.7 Keep setup minimal and focused

## 4.0 Unit Tests (Model Tests) [UNIT-TEST]

### 4.1 When to Write Unit Tests [UNIT-WHEN]
Only write unit tests for:
- 4.1.1 Complex business logic
- 4.1.2 Complex validations
- 4.1.3 State machines
- 4.1.4 Critical calculations
- 4.1.5 Data transformations

### 4.2 What to Skip [UNIT-SKIP]
- 4.2.1 Basic Rails validations
- 4.2.2 Simple associations
- 4.2.3 Attribute accessors
- 4.2.4 Basic scopes
- 4.2.5 Standard Rails callbacks

### 4.3 Best Practices [UNIT-BEST]
- 4.3.1 Focus on business rules
- 4.3.2 Test state changes
- 4.3.3 Test edge cases that matter
- 4.3.4 Use real objects and databases
- 4.3.5 Keep tests focused on one behavior

## 5.0 System Tests (Smoke Tests) [SYS-TEST]

### 5.1 Purpose [SYS-PURPOSE]
System tests verify basic end-to-end functionality and catch configuration issues that might prevent the system from working correctly.

### 5.2 When to Write System Tests [SYS-WHEN]
- 5.2.1 Critical user paths (e.g., signup, login)
- 5.2.2 Basic CRUD workflows
- 5.2.3 Essential business workflows
- 5.2.4 Public-facing features

### 5.3 Best Practices [SYS-BEST]
- 5.3.1 Keep system tests minimal
- 5.3.2 Focus on happy paths
- 5.3.3 Use for smoke testing
- 5.3.4 Don't try to cover edge cases
- 5.3.5 Accept that they'll be slower and more brittle

## 6.0 Test Data Management [DATA-MGT]

### 6.1 Factories Over Fixtures [DATA-FIX]
- 6.1.1 Use factories as your primary test data source
- 6.1.2 Keep factories descriptive and well-organized
- 6.1.3 Avoid over-defining unused traits

### 6.2 Test Helper Organization [DATA-HELP]
- 6.2.1 Keep helpers focused and simple
- 6.2.2 Avoid complex shared examples
- 6.2.3 Use plain Ruby methods over DSLs
- 6.2.4 Keep authentication helpers minimal
- 6.2.5 Avoid overuse of before/setup blocks

## 7.0 Authorization Testing [AUTH-TEST]

### 7.1 Key Points to Test [AUTH-KEY]
- 7.1.1 Resource ownership
- 7.1.2 Role-based access
- 7.1.3 Feature flags
- 7.1.4 Account-level permissions
- 7.1.5 API access tokens

### 7.2 Best Practices [AUTH-BEST]
- 7.2.1 Test each role explicitly
- 7.2.2 Verify both positive and negative cases
- 7.2.3 Test resource ownership boundaries
- 7.2.4 Check for proper error responses
- 7.2.5 Verify redirect paths

## 8.0 Multi-tenancy Testing [TENANT-TEST]

### 8.1 Purpose [TENANT-PURPOSE]
Multi-tenancy testing ensures proper data isolation and tenant-specific behavior, which is critical for secure and reliable operation of multi-tenant applications.

### 8.2 Key Aspects to Test [TENANT-ASPECTS]

#### 8.2.1 Data Isolation [TENANT-ISO]
- 8.2.1.1 Tenants cannot access other tenants' data
- 8.2.1.2 Queries are properly scoped to the current tenant
- 8.2.1.3 System functions correctly when identical data exists in multiple tenants

#### 8.2.2 Cross-tenant Authorization [TENANT-AUTH]
- 8.2.2.1 Appropriate 404s (not 403s) when accessing non-existent resources in current tenant
- 8.2.2.2 Proper handling of tenant-switching attempts
- 8.2.2.3 Correct tenant identification from request context

#### 8.2.3 Tenant-specific Behavior [TENANT-BEHAV]
- 8.2.3.1 Features behave correctly based on tenant settings
- 8.2.3.2 UI elements adapt to tenant configuration
- 8.2.3.3 Business rules respect tenant-specific variations

### 8.3 Best Practices [TENANT-BEST]
- 8.3.1 Test through controller actions to verify tenant scoping
- 8.3.2 Set up test factories to respect tenant boundaries
- 8.3.3 Create at least two tenants in test data for isolation checks
- 8.3.4 Test tenant identification from URLs, subdomains, or headers
- 8.3.5 Verify model scopes enforce tenant isolation
- 8.3.6 Keep tenant context setup simple and explicit in tests

## 9.0 Common Pitfalls to Avoid [PITFALLS]

### 9.1 Over-Testing [PIT-OVER]
- 9.1.1 Testing Rails defaults
- 9.1.2 Testing simple CRUD in isolation
- 9.1.3 Testing basic validations
- 9.1.4 Aiming for 100% coverage

### 9.2 Under-Testing [PIT-UNDER]
- 9.2.1 Skipping authorization
- 9.2.2 Missing critical paths
- 9.2.3 Insufficient controller tests
- 9.2.4 Ignoring error states

### 9.3 Poor Test Organization [PIT-ORG]
- 9.3.1 Complex shared examples
- 9.3.2 Excessive use of helpers
- 9.3.3 Unclear test names
- 9.3.4 Too much setup

### 9.4 Wrong Testing Level [PIT-LEVEL]
- 9.4.1 Too many system tests
- 9.4.2 Too many unit tests
- 9.4.3 Not enough controller tests
- 9.4.4 Missing smoke tests

## 10.0 Conclusion [CONCLUSION]

This testing approach emphasizes pragmatism over perfectionism, focusing on tests that provide real value to your application. The key points are:
- [CON-1] Controller tests are your primary testing layer
- [CON-2] Unit tests are only for complex business logic
- [CON-3] System tests are for smoke testing critical paths
- [CON-4] Use real objects over mocks/stubs
- [CON-5] Keep things simple and focused

Remember: The goal is not to test everything, but to test the right things at the right level, with an emphasis on testing through HTTP.
