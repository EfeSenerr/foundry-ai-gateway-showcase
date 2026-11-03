param suffix string
param tags object
param userEmail string
param budgetAmount int
param startDate string

resource ag 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-portops-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'portops'
    enabled: true
    emailReceivers: [
      { name: 'owner', emailAddress: userEmail, useCommonAlertSchema: true }
    ]
  }
}

var thresholds = [
  { key: 'actual50', threshold: 50, type: 'Actual' }
  { key: 'actual80', threshold: 80, type: 'Actual' }
  { key: 'actual100', threshold: 100, type: 'Actual' }
  { key: 'forecast100', threshold: 100, type: 'Forecasted' }
]

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: 'budget-portops-${suffix}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: toObject(thresholds, t => t.key, t => {
      enabled: true
      operator: 'GreaterThan'
      threshold: t.threshold
      thresholdType: t.type
      contactEmails: [userEmail]
      contactGroups: [ag.id]
    })
  }
}
