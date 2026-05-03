async function load(){
const res = await fetch('/data');
const data = await res.json();
const table = document.getElementById('table');
table.innerHTML = '';

data.forEach((c,i)=>{
const id = 'chart'+i;
table.innerHTML += `
<tr>
<td>${c.name}</td>
<td>$${c.current_price}</td>
<td>${c.price_change_percentage_24h.toFixed(2)}%</td>
<td><canvas id="${id}" width="100" height="40"></canvas></td>
</tr>`;

setTimeout(()=>{
new Chart(document.getElementById(id),{
type:'line',
data:{
labels:c.sparkline_in_7d.price,
datasets:[{data:c.sparkline_in_7d.price}]
},
options:{plugins:{legend:{display:false}},scales:{x:{display:false},y:{display:false}}}
});
},100);
});
}

setInterval(load,60000);
window.onload = load;
