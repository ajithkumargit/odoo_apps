/** @odoo-module **/

import {
    Component,
    useState,
    useRef,
    onMounted,
    onWillUnmount,
} from "@odoo/owl";

export class MultiSelect extends Component {

    static template = "expense_tracker.MultiSelect";

    static props = {
        groups: Array,
        selected: Array,
        placeholder: {
            type: String,
            optional: true,
        },
        onChange: Function,
    };

    setup() {

        this.root = useRef("root");

        this.state = useState({

            open: false,

            search: "",

            collapsed: {},
        });

        this._outsideClick = (ev) => {

            if (
                this.root.el &&
                !this.root.el.contains(ev.target)
            ) {
                this.state.open = false;
            }
        };

        onMounted(() => {

            document.addEventListener(
                "click",
                this._outsideClick
            );
        });

        onWillUnmount(() => {

            document.removeEventListener(
                "click",
                this._outsideClick
            );
        });
    }

    toggleDropdown(ev) {

        ev.stopPropagation();

        this.state.open = !this.state.open;
    }

    toggleGroup(id) {

        this.state.collapsed[id] =
            !this.state.collapsed[id];
    }

    isCollapsed(id) {

        return this.state.collapsed[id];
    }

    isSelected(id) {

        return this.props.selected.includes(id);
    }

    toggleItem(ev) {

        const id =
            parseInt(ev.target.dataset.id);

        let ids =
            [...this.props.selected];

        if (ids.includes(id)) {

            ids =
                ids.filter(x => x !== id);

        } else {

            ids.push(id);
        }

        this.props.onChange(ids);
    }

    clearSelection() {

        this.props.onChange([]);
    }

    get filteredGroups() {

        const txt =
            this.state.search
                .trim()
                .toLowerCase();

        if (!txt)
            return this.props.groups;

        return this.props.groups
            .map(group => ({

                ...group,

                children:
                    group.children.filter(

                        child =>

                            child.name
                                .toLowerCase()
                                .includes(txt)
                    )

            }))
            .filter(
                group =>
                    group.children.length
            );
    }

    get selectedText() {

        const selectedNames = [];

        this.props.groups.forEach(group => {

            group.children.forEach(item => {

                if (
                    this.props.selected.includes(
                        item.id
                    )
                ) {
                    selectedNames.push(item.name);
                }

            });

        });

        if (!selectedNames.length)
            return (
                this.props.placeholder
                || "Select"
            );

        if (
            selectedNames.length <= 2
        )
            return selectedNames.join(", ");

        return `${selectedNames[0]}, ${selectedNames[1]} +${selectedNames.length - 2}`;

    }

    groupSelectedCount(group) {

        return group.children.filter(

            c =>

                this.props.selected.includes(
                    c.id
                )

        ).length;
    }

}