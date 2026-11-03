// Part B: EUR budget -> hard stop.
// Cost budget / scheduled query alert -> action group -> Logic App -> APIM named value team-<team>-enabled=false -> 403 TeamBudgetExhausted.
param suffix string
param location string
param tags object
param userEmail string
param apimName string
param logAnalyticsWorkspaceId string

@description('Estimated spend per team and day (USD, from token metrics x illustrative prices) that triggers the kill switch')
param teamDailyThresholdsUsd object = {
  ops: 5
  it: 1
  cs: 2
}

@description('Create per-team Cost Management budgets filtered on the "project" tag (see docs: Foundry model spend is not tagged per project yet)')
param deployTeamBudgets bool = true
param teamBudgetAmount int = 25
param budgetStartDate string = utcNow('yyyy-MM-01')

var teams = ['ops', 'it', 'cs']
var apimServiceContributorRoleId = '312a565d-c81f-4fd8-895a-4e21e48d571c'
var apimArmBase = '${environment().resourceManager}subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.ApiManagement/service/${apimName}'

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

// ---------------------------------------------------------------- enforcer
resource enforcer 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-portops-budget-enforcer'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {}
      triggers: {
        When_a_budget_alert_or_manual_request_is_received: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {}
          }
        }
      }
      actions: {
        // Team name hint: manual body, alert custom property, alert rule name (e.g. ...-cs) or budget name (e.g. ...-cs).
        Get_team_hint_from_payload: {
          type: 'Compose'
          runAfter: {}
          inputs: '@toLower(coalesce(triggerBody()?[\'team\'], triggerBody()?[\'data\']?[\'customProperties\']?[\'team\'], triggerBody()?[\'data\']?[\'essentials\']?[\'alertRule\'], triggerBody()?[\'data\']?[\'BudgetName\'], \'\'))'
        }
        Resolve_team: {
          type: 'Compose'
          runAfter: { Get_team_hint_from_payload: ['Succeeded'] }
          inputs: '@if(or(equals(outputs(\'Get_team_hint_from_payload\'),\'ops\'),endsWith(outputs(\'Get_team_hint_from_payload\'),\'-ops\')),\'ops\',if(or(equals(outputs(\'Get_team_hint_from_payload\'),\'it\'),endsWith(outputs(\'Get_team_hint_from_payload\'),\'-it\')),\'it\',if(or(equals(outputs(\'Get_team_hint_from_payload\'),\'cs\'),endsWith(outputs(\'Get_team_hint_from_payload\'),\'-cs\')),\'cs\',\'\')))'
        }
        // This trigger can only disable; enable requests are rejected below (re-enable via la-portops-budget-reset).
        Resolve_action: {
          type: 'Compose'
          runAfter: { Resolve_team: ['Succeeded'] }
          inputs: 'false'
        }
        Is_team_known: {
          type: 'If'
          runAfter: { Resolve_action: ['Succeeded'] }
          expression: {
            and: [
              { not: { equals: ['@outputs(\'Resolve_team\')', ''] } }
              { not: { equals: ['@toLower(coalesce(triggerBody()?[\'action\'], \'\'))', 'enable'] } }
            ]
          }
          actions: {
            Set_APIM_named_value_team_enabled: {
              type: 'Http'
              runAfter: {}
              inputs: {
                method: 'PATCH'
                uri: '${apimArmBase}/namedValues/team-@{outputs(\'Resolve_team\')}-enabled?api-version=2024-05-01'
                headers: { 'Content-Type': 'application/json' }
                body: {
                  properties: {
                    value: '@{outputs(\'Resolve_action\')}'
                  }
                }
                authentication: {
                  type: 'ManagedServiceIdentity'
                  audience: environment().resourceManager
                }
              }
            }
            Respond_200_with_summary: {
              type: 'Response'
              runAfter: { Set_APIM_named_value_team_enabled: ['Succeeded'] }
              inputs: {
                statusCode: 200
                headers: { 'Content-Type': 'application/json' }
                body: {
                  team: '@{outputs(\'Resolve_team\')}'
                  namedValue: 'team-@{outputs(\'Resolve_team\')}-enabled'
                  value: '@{outputs(\'Resolve_action\')}'
                  action: 'disable'
                  reason: '@{coalesce(triggerBody()?[\'reason\'], triggerBody()?[\'data\']?[\'essentials\']?[\'alertRule\'], triggerBody()?[\'data\']?[\'BudgetName\'], \'alert\')}'
                }
              }
            }
          }
          else: {
            actions: {
              Respond_400_rejected: {
                type: 'Response'
                runAfter: {}
                inputs: {
                  statusCode: 400
                  headers: { 'Content-Type': 'application/json' }
                  body: {
                    error: '@{if(equals(toLower(coalesce(triggerBody()?[\'action\'], \'\')),\'enable\'),\'enable is not allowed via this trigger; use la-portops-budget-reset\',\'Could not resolve team (ops|it|cs) from payload\')}'
                    hint: '@{outputs(\'Get_team_hint_from_payload\')}'
                  }
                }
              }
            }
          }
        }
      }
      outputs: {}
    }
  }
}

resource enforcerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, enforcer.id, apimServiceContributorRoleId)
  scope: apim
  properties: {
    principalId: enforcer.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', apimServiceContributorRoleId)
  }
}

// ---------------------------------------------------------------- monthly reset
resource reset 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-portops-budget-reset'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {}
      triggers: {
        Manual_reenable: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              properties: { team: { type: 'string' } }
            }
          }
        }
        On_the_1st_of_every_month_00_05_UTC: {
          type: 'Recurrence'
          recurrence: {
            frequency: 'Month'
            interval: 1
            timeZone: 'UTC'
            schedule: {
              monthDays: [1]
              hours: [0]
              minutes: [5]
            }
          }
        }
      }
      actions: {
        For_each_team: {
          type: 'Foreach'
          runAfter: {}
          foreach: '@if(equals(toLower(coalesce(triggerBody()?[\'team\'], \'all\')),\'all\'),createArray(\'ops\',\'it\',\'cs\'),if(contains(createArray(\'ops\',\'it\',\'cs\'),toLower(triggerBody()?[\'team\'])),createArray(toLower(triggerBody()?[\'team\'])),createArray()))'
          actions: {
            Re_enable_team_named_value: {
              type: 'Http'
              runAfter: {}
              inputs: {
                method: 'PATCH'
                uri: '${apimArmBase}/namedValues/team-@{items(\'For_each_team\')}-enabled?api-version=2024-05-01'
                headers: { 'Content-Type': 'application/json' }
                body: { properties: { value: 'true' } }
                authentication: {
                  type: 'ManagedServiceIdentity'
                  audience: environment().resourceManager
                }
              }
            }
          }
        }
      }
      outputs: {}
    }
  }
}

resource resetRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apim.id, reset.id, apimServiceContributorRoleId)
  scope: apim
  properties: {
    principalId: reset.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', apimServiceContributorRoleId)
  }
}

// ---------------------------------------------------------------- action group
resource enforceAg 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-portops-budget-enforce'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'pobudget'
    enabled: true
    emailReceivers: [
      { name: 'owner', emailAddress: userEmail, useCommonAlertSchema: true }
    ]
    logicAppReceivers: [
      {
        name: 'budget-enforcer'
        resourceId: enforcer.id
        callbackUrl: listCallbackUrl('${enforcer.id}/triggers/When_a_budget_alert_or_manual_request_is_received', '2019-05-01').value
        useCommonAlertSchema: true
      }
    ]
  }
}

// ---------------------------------------------------------------- near-real-time trigger: estimated USD per team today
resource teamSpendAlerts 'Microsoft.Insights/scheduledQueryRules@2022-06-15' = [for team in teams: {
  name: 'alert-portops-spend-${team}'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'portops: estimated spend today for team ${team} above ${teamDailyThresholdsUsd[team]} USD'
    description: 'Tokens x illustrative list prices (gpt-4.1-mini 0.40/1.60, gpt-4.1 2.00/8.00 USD per 1M in/out). Fires the budget enforcer, which sets APIM named value team-${team}-enabled=false.'
    severity: 4
    enabled: true
    scopes: [logAnalyticsWorkspaceId]
    evaluationFrequency: 'PT5M'
    windowSize: 'P1D'
    autoMitigate: false
    muteActionsDuration: 'PT1H'
    criteria: {
      allOf: [
        {
          query: '''
let prices = datatable(Model:string, PriceIn:real, PriceOut:real) ["gpt-4.1-mini", 0.40, 1.60, "gpt-4.1", 2.00, 8.00];
AppMetrics
| where TimeGenerated >= startofday(now())
| where Name in ("Prompt Tokens", "Completion Tokens")
| extend Team = iff(tostring(Properties.Team) != "", tostring(Properties.Team), replace_string(tostring(Properties.Department), "dept-", "")), Model = tostring(Properties.Model)
| where Team == "${team}"
| lookup prices on Model
| extend Price = iff(Name == "Prompt Tokens", coalesce(PriceIn, 0.40), coalesce(PriceOut, 1.60))
| summarize EstimatedUsd = sum(Sum * Price / 1000000.0)
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'EstimatedUsd'
          operator: 'GreaterThan'
          threshold: teamDailyThresholdsUsd[team]
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [enforceAg.id]
      customProperties: {
        team: team
        thresholdUsd: string(teamDailyThresholdsUsd[team])
      }
    }
  }
}]

// ---------------------------------------------------------------- per-team Cost Management budgets (tag filter)
resource teamBudgets 'Microsoft.Consumption/budgets@2023-05-01' = [for team in teams: if (deployTeamBudgets) {
  name: 'budget-portops-${suffix}-${team}'
  properties: {
    category: 'Cost'
    amount: teamBudgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    filter: {
      tags: {
        name: 'project'
        operator: 'In'
        values: [team]
      }
    }
    notifications: {
      actual100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: [userEmail]
        contactGroups: [enforceAg.id]
      }
    }
  }
}]

// ---------------------------------------------------------------- diagnostics (same pattern as the FinOps lab)
resource enforcerDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-to-law'
  scope: enforcer
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ category: 'WorkflowRuntime', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

resource resetDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-to-law'
  scope: reset
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ category: 'WorkflowRuntime', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output enforcerName string = enforcer.name
output resetName string = reset.name
output actionGroupName string = enforceAg.name
