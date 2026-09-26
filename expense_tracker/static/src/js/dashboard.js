/** @odoo-module **/

import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { MultiSelect } from "../components/multi_select";

export class FinanceDashboard extends Component {
    static template = "expense_tracker.FinanceDashboard";

    static components = {
        MultiSelect,
    };

    setup() {
        this.state = useState({
            types: [],
            selectedTypes: [],

            categoryGroups: [],
            selectedCategories: [],

            accounts: [],
            selectedAccounts: [],

            xAxis: "transaction_datetime:month",
            chartType: "bar",

            chart: null,
        });

        this.orm = useService("orm");

        onWillStart(async () => {
            await Promise.all([
                this.loadTypes(),
                this.loadCategories(),
                this.loadAccounts(),
            ]);


        });
    }

    async loadCategories() {

        const categories = await this.orm.searchRead(
            "finance.category",
            [],
            ["name", "type_id"]
        );

        const groups = {};

        categories.forEach((cat) => {

            const typeId = cat.type_id?.[0] || 0;
            const typeName = cat.type_id?.[1] || "Others";

            if (!groups[typeId]) {
                groups[typeId] = {
                    id: typeId,
                    name: typeName,
                    children: [],
                };
            }

            groups[typeId].children.push({
                id: cat.id,
                name: cat.name,
            });

        });

        this.state.categoryGroups = Object.values(groups);
    }

    async loadAccounts() {

        this.state.accounts = await this.orm.searchRead(
            "finance.account",
            [],
            ["bankname"]
        );
    }
    async onCategoryUpdate(records) {
        this.state.selectedCategories = records.map(r => r.id);
        await this.loadChart();
    }
    async loadTypes() {

        this.state.types = (
            await this.orm.searchRead(
                "selection.model",
                [
                    ["constant", "=", "finance.transaction.type"]
                ],
                ["name"]
            )
        ).map(r => ({
            id: r.id,
            name: r.name,
        }));
    }
    async loadChart() {
        const result = await this.orm.call(
            "finance.transaction",
            "get_chart_data",
            [{
                x_axis: this.state.xAxis,
                category_ids: this.state.selectedCategories,
                account_ids: this.state.selectedAccounts,
                type_ids: this.state.selectedTypes,
            }]
        );

        this.renderChart(result);
    }

    renderChart(result) {

        const ctx =
            document.getElementById("financeChart");

        if (this.state.chart) {
            this.state.chart.destroy();
        }

        this.state.chart = new Chart(ctx, {
            type: this.state.chartType,
            data: {
                labels: result.labels,
                datasets: result.datasets,
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,

                layout: {
                    padding: 20,
                    overflow: "scroll",
                },

                plugins: {
                    legend: {
                        position: "top",
                    },
                },
            },
        });
    }

    async onTypeSelected(ids) {

        this.state.selectedTypes = ids;

        await this.loadChart();
    }
    async onXAxisChange(ev) {

        this.state.xAxis = ev.target.value;

        await this.loadChart();
    }

    async onChartTypeChange(ev) {

        this.state.chartType = ev.target.value;

        await this.loadChart();
    }

    async onTypeChange(ev) {

        this.state.typeId = ev.target.value;

        await this.loadChart();
    }

    async onAccountChange(ev) {

        this.state.selectedAccounts =
            [...ev.target.selectedOptions]
                .map(o => parseInt(o.value));

        await this.loadChart();
    }
    async onCategoryChange(ev) {

        this.state.selectedCategories =
            [...ev.target.selectedOptions]
                .map(o => parseInt(o.value));

        await this.loadChart();
    }
    async onCategorySelected(ids) {

        this.state.selectedCategories = ids;

        await this.loadChart();
    }

    async onAccountSelected(ids) {

        this.state.selectedAccounts = ids;

        await this.loadChart();
    }
    
}

FinanceDashboard.template =
    "expense_tracker.FinanceDashboard";

registry.category("actions").add(
    "finance_dashboard",
    FinanceDashboard
);